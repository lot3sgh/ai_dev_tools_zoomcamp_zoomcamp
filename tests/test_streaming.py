"""Phase 3 T2 — the streaming loop: answer_stream yields token/sql/outcome events.

Same deterministic stub seam as the smoke suite: no network, no key. Events are the
wire contract the chat service (health repo) relays over SSE.
"""

from __future__ import annotations

from assistant_core import Orchestrator, Outcome, StubProvider, Tool, ToolResult


class _EchoTool(Tool):
    name = "echo"
    description = "echoes the SQL back"

    def docs(self, few_shots: str = "") -> str:
        return "echo schema: nothing"

    def run(self, sql: str) -> ToolResult:
        if "bad" in sql:
            return ToolResult(error="syntax error at or near 'bad'")
        return ToolResult(rows=[(sql,)], row_count=1)


class _RawProvider:
    """Provider whose text has no action header at all (free-text answers)."""

    def __init__(self, text: str):
        self._text = text

    def name(self) -> str:
        return "raw"

    def complete(self, messages: list[dict]) -> str:
        return self._text

    def stream(self, messages: list[dict], *, session_id: str | None = None):
        for i in range(0, len(self._text), 4):
            yield self._text[i : i + 4]


def _events(question: str, turns: list[dict], **kw) -> list:
    return list(Orchestrator(StubProvider(turns=turns), _EchoTool(), **kw).answer_stream(question))


def _tokens(events) -> str:
    return "".join(e.text or "" for e in events if e.kind == "token")


def _final(events) -> Outcome:
    outcomes = [e.outcome for e in events if e.kind == "outcome"]
    assert outcomes, "no terminal outcome event"
    return outcomes[-1]


# ---------------------------------------------------------------- answer streams

def test_answer_streams_tokens_and_outcome():
    """The phrased answer arrives token-by-token; the header never leaks."""
    events = _events("q?", [{"answer": "hello world"}])
    assert _tokens(events) == "hello world"
    final = _final(events)
    assert final.text == "hello world"
    assert final.refusal is None and final.error is None
    assert all("ANSWER:" not in (e.text or "") for e in events)


def test_raw_text_without_header_streams_as_answer():
    """Free text not matching the action protocol is treated as the answer."""
    out = Orchestrator(_RawProvider("It was a good night."), _EchoTool()).answer("q?")
    assert out.text == "It was a good night."
    events = list(
        Orchestrator(_RawProvider("It was a good night."), _EchoTool()).answer_stream("q?")
    )
    assert _tokens(events) == "It was a good night."


# ---------------------------------------------------------------- sql turns

def test_sql_turn_emits_one_sql_event_and_streams_the_phrasing():
    """SQL is not streamed as tokens — it arrives as one executed-sql event."""
    events = _events("q?", [{"sql": "SELECT 1"}, {"answer": "one"}])
    assert [e.kind for e in events] == ["sql", "token", "outcome"]
    sql = events[0]
    assert sql.kind == "sql" and sql.sql == "SELECT 1"
    assert sql.row_count == 1 and sql.truncated is False
    assert sql.repairs == 0
    final = _final(events)
    assert final.text == "one"
    assert final.sql == "SELECT 1" and final.row_count == 1 and final.repairs == 0


def test_refusal_streams_text_and_terminal_outcome():
    events = _events("q?", [{"refuse": "out of my surface"}])
    assert _tokens(events) == "out of my surface"
    final = _final(events)
    assert final.refusal == "out of my surface"
    assert final.text is None


# ---------------------------------------------------------------- repair loop

def test_repair_rounds_surface_as_repeated_sql_events():
    """Each repair attempt is observable as its own sql event with a repair count."""
    events = _events("q?", [{"sql_err": "SELECT bad"}, {"sql_err": "SELECT bad"},
                            {"sql_err": "SELECT bad"}])
    sql_events = [e for e in events if e.kind == "sql"]
    assert [e.repairs for e in sql_events] == [0, 1, 2]
    assert not any(e.kind == "token" for e in events)
    final = _final(events)
    assert final.error and "syntax" in final.error.lower()
    assert final.repairs == 2


def test_repair_then_success():
    events = _events("q?", [{"sql_err": "SELECT bad"}, {"sql": "SELECT 1"},
                            {"answer": "repaired"}])
    sql_events = [e for e in events if e.kind == "sql"]
    assert [e.repairs for e in sql_events] == [0, 1]
    final = _final(events)
    assert final.text == "repaired" and final.repairs == 1


# ---------------------------------------------------------------- answer() parity

def test_answer_collects_the_stream_outcome():
    """answer() stays the synchronous shorthand and agrees with the stream."""
    scenarios = [
        [{"answer": "42"}],
        [{"sql": "SELECT 1"}, {"answer": "one"}],
        [{"refuse": "nope"}],
        [{"sql_err": "SELECT bad"}, {"sql_err": "SELECT bad"}, {"sql_err": "SELECT bad"}],
    ]
    for turns in scenarios:
        collected = Orchestrator(StubProvider(turns=turns), _EchoTool()).answer("q?")
        streamed = _final(_events("q?", turns))
        assert collected == streamed