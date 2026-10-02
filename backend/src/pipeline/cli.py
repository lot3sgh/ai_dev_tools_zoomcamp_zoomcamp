"""Pipeline CLI: `pipeline sync [--source local|drive] [--path DIR] [--limit N]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import config, db, state
from psycopg import sql
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
                sql.SQL("SELECT count(*) FROM bronze.{} WHERE _takeout = %s").format(
                    sql.Identifier(family)),
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


def run_eval(args: argparse.Namespace) -> int:
    """The eval-corpus gate (Phase 3, ticket 04): executed-row scoring on a fresh fixture DB.

    --self-check runs the deterministic stub path (no network, no key). The default uses the
    provider selected by the environment and skips cleanly when no LLM_API_KEY is present —
    that is the CI merge gate, blocking merges below 90% execution accuracy / 100% refusals.
    """
    from assistant import eval as evalmod, providers
    from assistant.providers import ProviderConfigError

    if args.self_check:
        factory = evalmod.stub_provider_factory()
    else:
        if not config.llm_api_key():
            return evalmod.gate_skip("LLM_API_KEY is not set")
        if not config.chatbot_password():
            print("eval: CHATBOT_DB_PASSWORD is not set — the corpus runs as the chatbot role",
                  file=sys.stderr)
            return 1
        try:
            provider = providers.build_provider()
        except ProviderConfigError as exc:
            print(f"eval: {exc}", file=sys.stderr)
            return 1
        factory = lambda pair: provider  # noqa: E731 - same provider for every pair

    db_name = args.fixture_db or evalmod.fixture_database_name()
    try:
        report = evalmod.run_gate(factory, db_name=db_name)
    except RuntimeError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 1
    evalmod.print_report(report)
    ok, message = evalmod.gate_verdict(evalmod.report_summary(report))
    print(message)
    return 0 if ok else 1


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

    eval_ = sub.add_parser(
        "eval",
        help="eval-corpus gate: judge the assistant by executed rows on the fixture DB",
    )
    eval_.add_argument(
        "--self-check", action="store_true",
        help="deterministic mechanics check: scripted stub, no network, no key",
    )
    eval_.add_argument(
        "--fixture-db", default=None,
        help="scratch database name for the corpus fixture (default: health_pipeline_eval)",
    )
    eval_.set_defaults(func=run_eval)
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