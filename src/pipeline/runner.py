"""Run pipeline steps for one takeout: bronze, then silver for curated families."""

from __future__ import annotations

import psycopg

from pipeline import ingest, silver


def process(conn: psycopg.Connection, info, source, _md5: str | None = None) -> dict:
    """Bronze-ingest a takeout, then build silver for the curated families it contains."""
    stats = ingest.process_takeout(conn, info, source)
    silver_stats: dict[str, dict[str, int]] = {}
    for family, entry in stats["entries"]:
        built = silver.build(conn, info.name, entry, family)
        if built.get("upserted") or built.get("rejected"):
            silver_stats[family] = built
    stats["silver"] = silver_stats
    return stats