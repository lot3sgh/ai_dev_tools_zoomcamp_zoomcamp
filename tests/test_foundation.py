"""Ticket 01 — Foundation: schemas, DDL idempotency, CLI exit codes."""

from __future__ import annotations

from pipeline import db


def _schemas(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name IN ('bronze','silver','pipeline')"
        )
        return {row[0] for row in cur.fetchall()}


def test_sync_against_empty_dir_creates_schemas(reset_db, tmp_path):
    from pipeline.cli import main

    rc = main(["sync", "--source", "local", "--path", str(tmp_path)])
    assert rc == 0
    with db.connect() as conn:
        assert _schemas(conn) == {"bronze", "silver", "pipeline"}


def test_ddl_is_idempotent(reset_db, tmp_path):
    from pipeline.cli import main

    assert main(["sync", "--source", "local", "--path", str(tmp_path)]) == 0
    assert main(["sync", "--source", "local", "--path", str(tmp_path)]) == 0


def test_sync_fails_nonzero_on_bad_source_path(reset_db):
    from pipeline.cli import main

    rc = main(["sync", "--source", "local", "--path", "/nonexistent/nope"])
    assert rc != 0