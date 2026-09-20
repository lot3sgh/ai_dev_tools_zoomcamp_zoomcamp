"""Bronze ingestion: stream a Takeout zip into family-per-table TEXT landing (ADR-0002)."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterator, Sequence

import psycopg
from psycopg import sql

PROVENANCE = ("_takeout", "_source_file", "_loaded_at")

_ENTRY_SKIP = ("__MACOSX",)


def family_from_path(entry_name: str) -> str:
    """The Family a CSV belongs to: its immediate takeout folder, slugified."""
    parts = entry_name.replace("\\", "/").split("/")
    folder = parts[-2] if len(parts) >= 2 else "root"
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", folder).strip("_").lower()
    if not slug:
        slug = "root"
    if slug[0].isdigit():
        slug = "r_" + slug
    return slug


def _column_names(header: Sequence[str]) -> list[str]:
    """Sanitize CSV headers into stable, unique, quoted-safe column names."""
    seen: set[str] = set()
    cols: list[str] = []
    for raw in header:
        col = re.sub(r"[^0-9a-zA-Z]+", "_", raw.strip()).strip("_").lower()
        if not col:
            col = "column"
        if col[0].isdigit() or col in PROVENANCE:
            col = "c_" + col
        orig = col
        n = 2
        while col in seen:
            col = f"{orig}_{n}"
            n += 1
        seen.add(col)
        cols.append(col)
    return cols


def ensure_bronze_table(conn: psycopg.Connection, family: str, header: Sequence[str]) -> None:
    """Create the family table if missing; widen it when drift adds columns."""
    cols = _column_names(header)
    coldefs = sql.SQL(", ").join(
        sql.SQL("{} TEXT").format(sql.Identifier(c)) for c in cols
    )
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS bronze.{} ({})").format(
                sql.Identifier(family), coldefs
            )
        )
        for c in cols:
            cur.execute(
                sql.SQL("ALTER TABLE bronze.{} ADD COLUMN IF NOT EXISTS {} TEXT").format(
                    sql.Identifier(family), sql.Identifier(c)
                )
            )
        for c in PROVENANCE:
            cur.execute(
                sql.SQL("ALTER TABLE bronze.{} ADD COLUMN IF NOT EXISTS {} TEXT").format(
                    sql.Identifier(family), sql.Identifier(c)
                )
            )
    conn.commit()


def _csv_rows(content: bytes) -> tuple[list[str], list[list[str]]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _to_csv(rows: list[list[str]], takeout: str, source_file: str) -> bytes:
    """Serialize rows + provenance as quoted CSV so empty cells stay empty strings."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    for row in rows:
        writer.writerow(list(row) + [takeout, source_file, "now"])
    return buf.getvalue().encode("utf-8")


def _load_family(
    conn: psycopg.Connection,
    family: str,
    header: list[str],
    rows: list[list[str]],
    takeout: str,
    source_file: str,
) -> int:
    cols = _column_names(header) + list(PROVENANCE)
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM bronze.{family} WHERE _source_file = %s", (source_file,)
        )  # delete-then-insert per source file
        with cur.copy(
            sql.SQL("COPY bronze.{} ({}) FROM STDIN WITH (FORMAT csv)").format(
                sql.Identifier(family),
                sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            )
        ) as copy:
            copy.write(_to_csv(rows, takeout, source_file))
    conn.commit()
    return len(rows)


def process_takeout(conn: psycopg.Connection, info, source) -> dict:
    """Ingest one takeout archive into bronze. Returns {rows, families, entries}."""
    path = source.open(info)
    families: dict[str, int] = {}
    entries: list[tuple[str, str]] = []
    total = 0
    with zipfile.ZipFile(path) as zf:
        for entry in zf.infolist():
            name = entry.filename
            if not name.lower().endswith(".csv"):
                continue
            if any(seg in _ENTRY_SKIP for seg in name.replace("\\", "/").split("/")):
                continue
            content = zf.read(entry)
            header, rows = _csv_rows(content)
            if not header:
                continue
            family = family_from_path(name)
            ensure_bronze_table(conn, family, header)
            n = _load_family(conn, family, header, rows, info.name, name)
            total += n
            families[family] = families.get(family, 0) + n
            entries.append((family, name))
    return {"rows": total, "families": families, "entries": entries}