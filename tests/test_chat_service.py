"""Phase 3 T2 — the chat service at its HTTP seam.

E2E through the FastAPI app with the deterministic stub provider against the throwaway
fixture DB: SSE framing (session/token/sql/outcome), follow-ups in a session, ephemeral
restart (sessions gone, Chat Log durable), thumbs round-trip to the Chat Log, and the
clean no-provider state. Same disciplines as the rest of the suite: no network, no key,
assert on HTTP responses + resulting database state, never on internals.
"""

from __future__ import annotations

import json
import os

from assistant_core import StubProvider

os.environ.setdefault("CHATBOT_DB_PASSWORD", "test_chatbot_pw")


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _client(turns: list[dict] | None = None, provider=None, session: dict | None = None):
    from fastapi.testclient import TestClient

    from assistant.server import create_app

    # provider wins if given; else a scripted stub when turns given; else None is passed
    # straight through so the no-provider config path is exercised.
    if provider is None and turns is not None:
        provider = StubProvider(turns=turns)
    app = create_app(provider=provider)
    client = TestClient(app)
    if session is not None:
        # the test may seed/read the app's in-memory sessions for restart semantics
        client.app.state.sessions = session
    return client


def _chat(client, question: str, session_id: str = "s1") -> tuple[int, str, list]:
    resp = client.post("/api/chat", json={"question": question, "session_id": session_id})
    events = _parse_sse(resp.text)
    return resp.status_code, resp.text, events


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        name, payload = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[len("data:"):].strip())
        if name:
            events.append((name, payload))
    return events


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ---------------------------------------------------------------- the page

def test_home_serves_the_single_file_ui():
    client = _client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "<!doctype html" in html.lower()
    assert 'name="viewport"' in html
    # single self-contained page: no external assets, no framework
    assert '<script src=' not in html
    assert '<link rel="stylesheet"' not in html


def test_no_provider_is_a_clean_state_not_a_crash(monkeypatch):
    """LLM not configured -> the page still serves; chat returns a clean 503 JSON."""
    from assistant import providers

    def _explode():
        raise providers.ProviderConfigError("LLM_BASE_URL is not set")

    monkeypatch.setattr(providers, "build_provider", _explode)
    client = _client(provider=None)  # create_app falls back to build_provider
    assert client.get("/").status_code == 200
    resp = client.post("/api/chat", json={"question": "hello?", "session_id": "s"})
    assert resp.status_code == 503
    assert "LLM_BASE_URL" in resp.json()["detail"]


# ---------------------------------------------------------------- the stream

def test_chat_streams_sql_tokens_and_outcome(reset_db, source_dir):
    assert _sync(source_dir) == 0
    client = _client(turns=[
        {"sql": "SELECT count(*) AS days FROM gold.daily_health"},
        {"answer": "There are 7 health days."},
    ])
    status, _, events = _chat(client, "how many health days are there?", "session-a")
    assert status == 200
    names = [name for name, _ in events]
    assert names[0] == "session" and events[0][1]["session_id"] == "session-a"
    assert "sql" in names
    sql_event = next(p for n, p in events if n == "sql")
    assert "daily_health" in sql_event["sql"] and sql_event["rows"] == 1
    tokens = "".join(p["chunk"] for n, p in events if n == "token")
    assert tokens == "There are 7 health days."
    outcome = next(p for n, p in events if n == "outcome")
    assert outcome["outcome"] == "answered"
    assert outcome["text"] == "There are 7 health days."
    assert outcome["row_count"] == 1 and outcome["log_id"] is not None
    # the exchange is in the Chat Log, exactly once, with provider line
    from pipeline import db

    with db.connect() as conn:
        rows = _q(conn, "SELECT provider, question, outcome, sql, row_count, session_id, "
                        "thumbs FROM pipeline.chat_log")
        assert len(rows) == 1
        assert rows[0][0] == "stub"
        assert rows[0][2] == "answered"
        assert "daily_health" in rows[0][3]
        assert rows[0][4] == 1
        assert rows[0][5] == "session-a"


def test_refusal_streams_and_logs_refused(reset_db, source_dir):
    assert _sync(source_dir) == 0
    client = _client(turns=[{"refuse": "blood pressure is out of my surface"}])
    _, _, events = _chat(client, "what is my blood pressure?")
    outcome = next(p for n, p in events if n == "outcome")
    assert outcome["outcome"] == "refused"
    assert "blood pressure is out of my surface" in outcome["detail"]
    from pipeline import db

    with db.connect() as conn:
        rows = _q(conn, "SELECT outcome, detail FROM pipeline.chat_log")
        assert rows == [("refused", "blood pressure is out of my surface")]


def test_repair_attempts_surface_as_sql_events(reset_db, source_dir):
    assert _sync(source_dir) == 0
    client = _client(turns=[
        {"sql_err": "SELECT FROM WHERE"},
        {"sql": "SELECT count(*) AS days FROM gold.daily_health"},
        {"answer": "There are 7 health days."},
    ])
    _, _, events = _chat(client, "how many health days?")
    sqls = [p for n, p in events if n == "sql"]
    assert [p["repairs"] for p in sqls] == [0, 1]
    outcome = next(p for n, p in events if n == "outcome")
    assert outcome["outcome"] == "answered" and outcome["repairs"] == 1


# ---------------------------------------------------------------- sessions

def test_follow_up_sees_the_previous_turns(reset_db, source_dir):
    """Relative references resolve: turn 2 receives turn 1's question and answer."""
    assert _sync(source_dir) == 0
    seen: list[list[dict]] = []

    class _Recording(StubProvider):
        def stream(self, messages):
            seen.append(messages)
            return super().stream(messages)

    client = _client(provider=_Recording(turns=[
        {"answer": "Your deepest sleep was on the 20th."},
        {"answer": "It was 93 minutes — 7 more than usual."},
    ]))
    _chat(client, "how deep did I sleep?", "session-r")
    _chat(client, "and compared to the night before?", "session-r")
    second = seen[1]
    roles = [(m["role"], m["content"]) for m in second]
    assert ("user", "how deep did I sleep?") in roles
    assert ("assistant", "Your deepest sleep was on the 20th.") in roles
    assert roles[-1] == ("user", "and compared to the night before?")


def test_restart_loses_sessions_but_not_the_chat_log(reset_db, source_dir):
    assert _sync(source_dir) == 0
    seen: list[list[dict]] = []

    class _Recording(StubProvider):
        def stream(self, messages):
            seen.append(messages)
            return super().stream(messages)

    first = _client(provider=_Recording(turns=[{"answer": "A1"}]))
    _chat(first, "q1?", "session-r")
    # a restart builds a new app: in-memory sessions are gone by design
    second = _client(provider=_Recording(turns=[{"answer": "A2"}]))
    _chat(second, "q2?", "session-r")
    users = [m["content"] for m in seen[-1] if m["role"] == "user"]
    assert users == ["q2?"]  # the previous session's turns are gone after restart
    from pipeline import db

    with db.connect() as conn:
        rows = _q(conn, "SELECT question, outcome FROM pipeline.chat_log ORDER BY id")
        assert [r[0] for r in rows] == ["q1?", "q2?"]  # durable
        assert all(r[1] == "answered" for r in rows)


# ---------------------------------------------------------------- thumbs

def test_thumbs_round_trip_to_the_chat_log(reset_db, source_dir):
    assert _sync(source_dir) == 0
    client = _client(turns=[{"answer": "ok"}])
    _, _, events = _chat(client, "thumbs me", "session-t")
    log_id = next(p for n, p in events if n == "outcome")["log_id"]
    resp = client.post("/api/feedback", json={"log_id": log_id, "thumbs": "down"})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    from pipeline import db

    with db.connect() as conn:
        assert _q(conn, "SELECT thumbs FROM pipeline.chat_log WHERE id = %s", (log_id,)) \
            == [("down",)]
    # a second rating for the same row is refused (one rating per exchange)
    resp = client.post("/api/feedback", json={"log_id": log_id, "thumbs": "up"})
    assert resp.status_code == 404
    resp = client.post("/api/feedback", json={"log_id": 999999, "thumbs": "up"})
    assert resp.status_code == 404


def test_empty_question_is_rejected():
    client = _client()
    resp = client.post("/api/chat", json={"question": "", "session_id": "s"})
    assert resp.status_code == 422