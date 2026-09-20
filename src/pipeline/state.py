"""Sync engine: file diff against the ledger, then process what's due (ADR-0003)."""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from pipeline import db
from pipeline.sources import FileInfo


@dataclass
class RunSummary:
    processed: list[FileInfo] = field(default_factory=list)
    skipped: list[FileInfo] = field(default_factory=list)
    failed: list[FileInfo] = field(default_factory=list)


def needs_processing(conn: psycopg.Connection, info: FileInfo) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT md5, status FROM pipeline.processed_files WHERE file_id = %s",
            (info.id,),
        )
        row = cur.fetchone()
    if row is None:
        return True  # new file
    md5, status = row
    if status == "error":
        return True  # failed runs retried on the next run
    return md5 != info.md5  # changed content


def record(
    conn: psycopg.Connection,
    info: FileInfo,
    status: str,
    row_count: int = 0,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline.processed_files
                (file_id, name, modified_time, md5, status, row_count, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (file_id) DO UPDATE SET
                name = EXCLUDED.name,
                modified_time = EXCLUDED.modified_time,
                md5 = EXCLUDED.md5,
                status = EXCLUDED.status,
                row_count = EXCLUDED.row_count,
                error = EXCLUDED.error,
                processed_at = now()
            """,
            (info.id, info.name, info.modified_time, info.md5, status, row_count, error),
        )
    conn.commit()


def sync(source, ingest_takeout) -> RunSummary:
    """Diff the source catalog against the ledger and process the frontier."""
    summary = RunSummary()
    catalog = source.catalog()
    with db.connect() as conn:
        for info in catalog:
            if not needs_processing(conn, info):
                summary.skipped.append(info)
                continue
            try:
                stats = ingest_takeout(conn, info, source, info.md5)
                record(conn, info, "processed", row_count=stats["rows"])
                summary.processed.append(info)
            except Exception as exc:  # noqa: BLE001 - ledger must capture any failure
                record(conn, info, "error", error=str(exc))
                summary.failed.append(info)
    return summary