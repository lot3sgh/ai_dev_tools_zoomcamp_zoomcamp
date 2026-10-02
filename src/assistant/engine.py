"""The Health Assistant engine wiring (Phase 3).

Answers a question through the generic core (Orchestrator + registry) and records every
exchange in the Chat Log — the audit trail, eval-corpus seed, and provider line. Ticket 02
added the streaming variant: the SSE service relays `token` events live, one `sql` event
per executed query, and the terminal `outcome` event; `log_exchange` returns the Chat Log
row id so thumbs can round-trip through the service.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
from assistant_core import Orchestrator, Outcome, Provider, Registry, StreamEvent

from assistant.tool import RunHealthQuery
from pipeline import db


def chatbot_conninfo() -> str:
    """Connection string for the read-only chatbot role (env-provided password)."""
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


def make_orchestrator(provider: Provider, *, registry: Registry | None = None,
                      few_shots: str = "", **rails) -> Orchestrator:
    """An orchestrator wired to the registered run_health_query tool (health registration)."""
    reg = register_health_tools(Registry()) if registry is None else registry
    tool = reg.get("run_health_query")
    assert tool is not None, "run_health_query not registered"
    return Orchestrator(provider, tool, few_shots=few_shots)


def answer_stream(
    question: str,
    provider: Provider,
    *,
    session_id: str = "anon",
    registry: Registry | None = None,
    session: list[dict] | None = None,
    few_shots: str = "",
    **rails,
) -> Iterator[StreamEvent]:
    """Streaming answer: token/sql/outcome events. Caller owns Chat Log logging."""
    orch = make_orchestrator(provider, registry=registry, few_shots=few_shots, **rails)
    yield from orch.answer_stream(question, session)


def answer_question(
    question: str,
    provider: Provider,
    *,
    session_id: str = "anon",
    registry: Registry | None = None,
    session: list[dict] | None = None,
    few_shots: str = "",
    **rails,
) -> Outcome:
    """Ask the assistant; every exchange lands in pipeline.chat_log (logs the outcome)."""
    outcome: Outcome | None = None
    for event in answer_stream(question, provider, session_id=session_id, registry=registry,
                               session=session, few_shots=few_shots, **rails):
        if event.kind == "outcome":
            outcome = event.outcome
    assert outcome is not None, "stream ended without a terminal outcome"
    log_exchange(session_id, provider.name(), question, outcome)
    return outcome


_OUTCOME = {"answer": "answered", "refusal": "refused", "error": "error"}


def log_exchange(session_id: str, provider_name: str, question: str, out: Outcome) -> int:
    """Record one exchange in the Chat Log; returns the row id (thumbs target)."""
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
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (session_id, provider_name, question, out.sql, out.row_count, outcome, detail),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("chat_log insert returned no id")
            return row[0]