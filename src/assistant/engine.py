"""The Health Assistant engine wiring (Phase 3, ticket 01).

Answers a question through the generic core (Orchestrator + registry) and records every
exchange in the Chat Log — the audit trail, eval-corpus seed, and provider line.
"""

from __future__ import annotations

from assistant_core import Orchestrator, Outcome, Provider, Registry, ToolResult

from assistant.tool import RunHealthQuery
from pipeline import db


def chatbot_conninfo() -> str:
    """Connection string for the read-only chatbot role (env-provided password)."""
    import psycopg

    from pipeline import config

    password = config.chatbot_password()
    if not password:
        raise RuntimeError("CHATBOT_DB_PASSWORD is not set — the assistant role cannot connect")
    kw = {k: config.db_env()[k] for k in ("dbname", "host", "port")}
    kw.update(user="chatbot", password=password)
    return psycopg.conninfo.make_conninfo(**kw)


def make_tool(
    chatbot_conn: str | None = None,
    *,
    timeout_s: float = 10,
    row_cap: int = 500,
    cardinality_guard: int = 1_000_000,
) -> RunHealthQuery:
    return RunHealthQuery(
        chatbot_conn or chatbot_conninfo(),
        timeout_s=timeout_s,
        row_cap=row_cap,
        cardinality_guard=cardinality_guard,
    )


def register_health_tools(registry: Registry, *, timeout_s: float = 10, row_cap: int = 500,
                          cardinality_guard: int = 1_000_000) -> Registry:
    """The health registration seam: add run_health_query to a capability Registry."""
    registry.register(make_tool(timeout_s=timeout_s, row_cap=row_cap,
                                cardinality_guard=cardinality_guard))
    return registry


_OUTCOME = {"answer": "answered", "refusal": "refused", "error": "error"}


def answer_question(
    question: str,
    provider: Provider,
    *,
    session_id: str = "anon",
    registry: Registry | None = None,
) -> Outcome:
    """Ask the assistant; every exchange lands in pipeline.chat_log."""
    reg = register_health_tools(registry) if registry is None else registry
    tool = reg.get("run_health_query")
    assert tool is not None, "run_health_query not registered"
    out = Orchestrator(provider, tool).answer(question)

    _log_exchange(session_id, provider.name(), question, out)
    return out


def _log_exchange(session_id: str, provider: str, question: str, out: Outcome) -> None:
    if out.text is not None:
        outcome, detail = _OUTCOME["answer"], None
    elif out.refusal is not None:
        outcome, detail = _OUTCOME["refusal"], out.refusal
    else:
        outcome, detail = _OUTCOME["error"], out.error
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline.chat_log"
                " (session_id, provider, question, sql, row_count, outcome, detail)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (session_id, provider, question, out.sql, out.row_count, outcome, detail),
            )