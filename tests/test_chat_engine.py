"""T1 (tracer bullet) — chat engine E2E: chatbot role, run_health_query rails, stub round trip.

Same seam as the rest of the suite: run the CLI against the fixture Takeout (fresh schemas +
gold views + provisioned roles), then drive the engine through the capability registry with a
scripted stub provider. No network, no API key.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from assistant_core import Registry, StubProvider

# conftest provisions the dashboard role; the assistant needs its own read-only role.


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _chatbot_conn(chatbot_password: str = "test_chatbot_pw"):
    from pipeline import config, db

    kw = {k: config.db_env()[k] for k in ("dbname", "host", "port")}
    kw.update(user="chatbot", password=chatbot_password)
    return psycopg.conninfo.make_conninfo(**kw)


def _registry(**rails):
    from assistant import register_health_tools

    return register_health_tools(Registry(), **rails)


def _answer(question: str, turns: list[dict], *, session_id: str = "test", **rails):
    from assistant import answer_question

    return answer_question(
        question, StubProvider(turns=turns), session_id=session_id,
        registry=_registry(**rails),
    )


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ---------------------------------------------------------------- role & surface

def test_chatbot_role_enforces_the_semantic_layer(reset_db, source_dir):
    """SELECT works on the layer, INSERT and bronze are denied by grants."""
    assert _sync(source_dir) == 0
    conn = psycopg.connect(_chatbot_conn(), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM gold.daily_health")
            assert cur.fetchone()[0] >= 1
            cur.execute("SELECT count(*) FROM silver.sleep_score")
            assert cur.fetchone()[0] == 3      # 4 bronze rows, 1 deliberately rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO silver.sleep_score (sleep_log_entry_id) VALUES (1)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM bronze.sleep_score")
    finally:
        conn.close()


def test_grain_comments_are_in_the_database(reset_db, source_dir):
    """The tool's model-facing docs come from the DB, not a prompt file."""
    assert _sync(source_dir) == 0
    from assistant import make_tool

    tool = make_tool(chatbot_conn=_chatbot_conn())
    docs = tool.docs()
    assert "night" in docs.lower()          # sleep_summary grain
    assert "daily_health" in docs           # surface listed
    assert "sleep_score" in docs            # whitelisted silver surface


# ---------------------------------------------------------------- engine round trip

def test_engine_round_trip_answers_from_fixture(reset_db, source_dir):
    """Question -> stub SQL -> rows from the fixture DB -> phrased answer."""
    assert _sync(source_dir) == 0
    out = _answer(
        "how many health days are there?",
        [{"sql": "SELECT count(*) AS days FROM gold.daily_health"},
         {"answer": "There are 7 health days."}],
    )
    assert out.text == "There are 7 health days."
    assert out.sql == "SELECT count(*) AS days FROM gold.daily_health"
    assert out.row_count == 1
    assert out.repairs == 0


def test_repair_loop_fixes_bad_sql(reset_db, source_dir):
    """A syntax error triggers the repair path, bounded; then answers."""
    assert _sync(source_dir) == 0
    out = _answer(
        "what is my stress today?",
        [{"sql_err": "SELECT FROM WHERE"},
         {"sql": "SELECT night, overall_score FROM gold.sleep_summary ORDER BY night LIMIT 1"},
         {"answer": "Your latest stress score is 67."}],
    )
    assert out.text == "Your latest stress score is 67."
    assert out.repairs == 1


def test_repair_budget_exhausted_becomes_error(reset_db, source_dir):
    assert _sync(source_dir) == 0
    out = _answer(
        "q?",
        [{"sql_err": "SELECT FROM WHERE"},
         {"sql_err": "SELECT FROM WHERE"},
         {"sql_err": "SELECT FROM WHERE"}],
    )
    assert out.text is None and out.refusal is None
    assert out.error and "syntax" in out.error.lower()
    assert out.repairs == 2


# ---------------------------------------------------------------- rails

def test_cardinality_guard_refuses_unbounded_queries(reset_db, source_dir):
    """Estimated rows above the guard are refused before execution."""
    assert _sync(source_dir) == 0
    out = _answer(
        "give me everything",
        [{"sql": "SELECT * FROM gold.daily_health"}],
        cardinality_guard=5,
    )
    assert out.refusal and "rows" in out.refusal.lower()
    assert out.text is None


def test_row_cap_truncates_with_disclosure(reset_db, source_dir):
    assert _sync(source_dir) == 0
    out = _answer(
        "list the health days",
        [{"sql": "SELECT date FROM gold.daily_health ORDER BY date"},
         {"answer": "Here are the first days."}],
        row_cap=3,
    )
    assert out.row_count == 3
    assert out.truncated is True


def test_timeout_aborts_slow_query(reset_db, source_dir):
    assert _sync(source_dir) == 0
    out = _answer(
        "slow question",
        [{"sql": "SELECT pg_sleep(5) FROM gold.daily_health"}],
        timeout_s=0.5,
    )
    assert out.text is None
    assert out.error and "canceling" in out.error.lower()


# ---------------------------------------------------------------- chat log

def test_chat_log_records_every_exchange(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    _answer("how many health days?",
            [{"sql": "SELECT count(*) FROM gold.daily_health"}, {"answer": "7"}])
    _answer("out of surface?",
            [{"refuse": "I cannot answer that."}])
    with db.connect() as conn:
        rows = _q(conn, "SELECT provider, question, outcome, sql, row_count, session_id "
                        "FROM pipeline.chat_log ORDER BY id")
        assert len(rows) == 2
        assert rows[0][0] == "stub"
        assert rows[0][1] == "how many health days?"
        assert rows[0][2] == "answered"
        assert "daily_health" in rows[0][3]
        assert rows[0][4] == 1
        assert rows[1][2] == "refused"
        assert rows[1][5] == "test"


def test_tool_registers_through_the_capability_registry(reset_db, source_dir):
    from assistant_core import Orchestrator

    reg = _registry()
    assert reg.names() == ["run_health_query"]
    tool = reg.get("run_health_query")
    assert tool is not None and "sql" in tool.description.lower()