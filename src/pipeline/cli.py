"""Pipeline CLI: `pipeline sync [--source local|drive] [--path DIR] [--limit N]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import config, db, state
from pipeline.runner import process
from pipeline.sources import Source


def _bronze_summary(conn, takeout: str) -> dict[str, int]:
    """Per-family bronze row counts for one takeout archive."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'bronze' ORDER BY table_name"
        )
        families = [r[0] for r in cur.fetchall()]
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for family in families:
            cur.execute(
                "SELECT count(*) FROM bronze.%s WHERE _takeout = %%s" % family,
                (takeout,),
            )
            counts[family] = cur.fetchone()[0]
    return {k: v for k, v in counts.items() if v}


def _rejected_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM silver.rejected_rows")
        return cur.fetchone()[0]


def run_sync(args: argparse.Namespace) -> int:
    if args.source == "local":
        from pipeline.sources import LocalSource

        source: Source = LocalSource(Path(args.path), limit=args.limit)
    else:
        from pipeline.drive import DriveSource

        source = DriveSource.make(limit=args.limit)

    db.ensure_schemas()
    summary = state.sync(source, process)

    print(f"Takeouts processed: {len(summary.processed)}"
          f" | skipped: {len(summary.skipped)} | failed: {len(summary.failed)}")

    with db.connect() as conn:
        for info in summary.processed:
            print(f"  {info.name}: rows={_bronze_summary(conn, info.name)}")
        rejected = _rejected_count(conn)
        if rejected:
            print(f"  silver.rejected_rows: {rejected}")

    if summary.failed:
        print("Failures:", file=sys.stderr)
        with db.connect() as conn:
            for info in summary.failed:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT error FROM pipeline.processed_files WHERE file_id = %s",
                        (info.id,),
                    )
                    err = cur.fetchone()
                print(f"  {info.name}: {err[0] if err else 'unknown error'}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="ingest new/changed takeouts")
    sync.add_argument(
        "--source", choices=["local", "drive"], default="local",
        help="where takeouts come from (default: local)",
    )
    sync.add_argument("--path", default=None, help="local directory of takeout zips")
    sync.add_argument("--limit", type=int, default=None, help="process at most N files (dev)")
    sync.set_defaults(func=run_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        return args.func(args)
    except SystemExit as exc:  # argparse error path -> non-zero
        return int(exc.code or 1)
    except Exception as exc:  # noqa: BLE001 - top-level guard
        print(f"pipeline: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())