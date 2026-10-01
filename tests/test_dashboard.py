"""Phase 0b ticket 02 — read-only dashboard role: SELECT on silver+gold, nothing else."""

from __future__ import annotations

import os

import psycopg

from tests.conftest import make_zip


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _dashboard_conninfo():
    from pipeline import config

    return psycopg.conninfo.make_conninfo(
        dbname=config.db_env()["dbname"],
        host=config.db_env()["host"],
        port=config.db_env()["port"],
        user="dashboard",
        password=os.environ["DASHBOARD_DB_PASSWORD"],
    )


def test_dashboard_role_reads_gold_but_writes_and_bronze_are_denied(reset_db, source_dir):
    """The sync provisions an idempotent role; reads work, writes and bronze don't."""
    assert _sync(source_dir) == 0
    with psycopg.connect(_dashboard_conninfo(), autocommit=True) as conn:
        with conn.cursor() as cur:
            # SELECT on gold and silver works
            cur.execute("SELECT count(*) FROM gold.daily_health")
            assert cur.fetchone()[0] >= 1
            cur.execute("SELECT count(*) FROM silver.stress")
            assert cur.fetchone()[0] == 2
            # explicit grant check on an updatable silver table: SELECT yes, INSERT no
            cur.execute(
                "SELECT has_table_privilege(current_user, 'silver.stress', 'SELECT'),"
                "       has_table_privilege(current_user, 'silver.stress', 'INSERT')"
            )
            sel, ins = cur.fetchone()
            assert sel is True and ins is False
            # mutation on an updatable silver table is denied at the server
            with conn.cursor() as cur2:
                try:
                    cur2.execute("INSERT INTO silver.stress (date) VALUES ('2026-01-01')")
                    raise AssertionError("INSERT into silver should be denied")
                except psycopg.errors.InsufficientPrivilege:
                    pass
            # bronze stays invisible to the analytics login
            with conn.cursor() as cur3:
                try:
                    cur3.execute("SELECT 1 FROM bronze.sleep_score")
                    raise AssertionError("SELECT from bronze should be denied")
                except psycopg.errors.InsufficientPrivilege:
                    pass
    # provisioning is idempotent: a second sync leaves the role fully working
    assert _sync(source_dir) == 0
    with psycopg.connect(_dashboard_conninfo(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM gold.freshness")
            assert cur.fetchone()[0] == 1