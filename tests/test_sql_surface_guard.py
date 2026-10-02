"""Unit + integration contract for the SQL-surface guard (agent-hooks).

The guard is defense in depth in front of the EXPLAIN dry-run — this test file is the
contract the run_health_query tool relies on. The role-grant enforcements stay covered
by the psycopg-level permission tests in test_chat_engine.
"""

from __future__ import annotations

from agent_hooks.sql_surface_guard import guard_sql, surface_docs
import pytest




def test_accepts_plain_reads():
    assert guard_sql("SELECT count(*) FROM gold.daily_health") == (True, "")
    assert guard_sql("SELECT night, overall_score FROM gold.sleep_summary "
                     "WHERE night = '2026-09-19'") == (True, "")
    assert guard_sql("WITH recent AS (SELECT night FROM gold.sleep_summary) "
                     "SELECT count(*) FROM recent") == (True, "")
    assert guard_sql("SELECT device_type FROM silver.device") == (True, "")


def test_refuses_writes_and_ddl():
    for sql in (
        "INSERT INTO gold.daily_health VALUES (1)",
        "UPDATE silver.profile SET height = 1",
        "DELETE FROM silver.device",
        "DROP TABLE gold.daily_health",
        "TRUNCATE silver.device",
        "GRANT SELECT ON gold.daily_health TO public",
    ):
        ok, reason = guard_sql(sql)
        assert not ok and "refused" in reason.lower()


def test_refuses_multiple_statements():
    ok, reason = guard_sql("SELECT 1; SELECT 2")
    assert not ok and "multi-statement" in reason


def test_refuses_non_read_leaders():
    ok, reason = guard_sql("VACUUM gold.daily_health")
    assert not ok
    ok, reason = guard_sql("EXPLAIN SELECT 1")   # explain is an allowed read leader
    assert ok


def test_refuses_relations_outside_the_semantic_layer():
    ok, reason = guard_sql("SELECT * FROM bronze.sleep_score")
    assert not ok and "bronze" in reason
    ok, reason = guard_sql("SELECT * FROM silver.hrv")
    assert not ok and "hrv" in reason
    ok, reason = guard_sql("SELECT * FROM gold.nonexistent")
    assert not ok


def test_empty_and_garbage():
    assert guard_sql("")[0] is False
    assert guard_sql("   ")[0] is False
    ok, reason = guard_sql("SELECT count(*) FROM gold.daily_health;")
    assert ok  # a single trailing semicolon is stripped by the tool before the guard


def test_surface_docs_are_user_readable():
    docs = surface_docs()
    assert "daily_health" in docs and "sleep_score" in docs
    assert "bronze" in docs.lower()  # explicitly lists what is NOT reachable


# ---------------------------------------------------------------- through the tool

def _chatbot_conn():
    from pipeline import config
    import psycopg

    kw = {k: config.db_env()[k] for k in ("dbname", "host", "port")}
    kw.update(user="chatbot", password=config.chatbot_password() or "test_chatbot_pw")
    return psycopg.conninfo.make_conninfo(**kw)


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


@pytest.mark.integration
def test_tool_refuses_a_write_before_the_database(reset_db, source_dir):
    """The guard refuses at the tool layer — even the EXPLAIN dry-run is skipped."""
    assert _sync(source_dir) == 0
    from assistant import make_tool

    tool = make_tool(chatbot_conn=_chatbot_conn())
    result = tool.run("INSERT INTO silver.device (wire_id) VALUES ('x')")
    assert result.refusal and "refused" in result.refusal
    assert result.error is None