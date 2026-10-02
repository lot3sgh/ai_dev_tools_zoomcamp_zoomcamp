"""The Health Assistant HTTP service (Phase 3, ticket 02).

FastAPI + SSE over the engine from ticket 01: a streaming chat endpoint (question in,
token stream out, terminal outcome event carrying the Chat Log id for thumbs), a single
self-contained mobile-friendly page served from the same service, and in-memory sessions
so follow-ups resolve relative references ("…and the week before?").

Design contracts (spec decisions 8-11):
- Sessions are in-memory and ephemeral; a restart loses them, never the Chat Log.
- Thumbs per answer write to the Chat Log (one rating per exchange).
- No configured provider is a clean page-level state, not a crash (503 + JSON detail).
- The provider records itself on every Chat Log row via its name().
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from assistant_core import Outcome, Provider, Registry

from assistant import corpus, engine, providers
from assistant.providers import ProviderConfigError
from pipeline import db

MAX_TURNS = 12                 # bounded conversation context per in-memory session
UI_PATH = Path(__file__).resolve().parent / "web" / "ui.html"


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = "anon"


class FeedbackRequest(BaseModel):
    log_id: int
    thumbs: str = Field(pattern="^(up|down)$")


def create_app(provider: Provider | None = None, *, registry: Registry | None = None) -> FastAPI:
    """Build the service. `provider` is injected by tests; otherwise built from env.

    No provider built = app state "assistant offline" (page still serves; /api/chat 503s
    with the configuration reason in the detail).
    """
    app = FastAPI(title="Health Assistant", version="0.3.0")
    from pipeline import config

    config.load_env()  # same bootstrap as the pipeline CLI: .env, then the environment wins
    app.state.provider_error = None
    if provider is None:
        try:
            provider = providers.build_provider()
        except ProviderConfigError as exc:
            app.state.provider_error = str(exc)
    app.state.provider = provider
    app.state.registry = registry
    app.state.few_shots = corpus.few_shots()
    app.state.sessions = {}  # session_id -> bounded list of user/assistant turns

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(UI_PATH, media_type="text/html")

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> StreamingResponse:
        if app.state.provider is None:
            detail = app.state.provider_error or "no LLM provider configured"
            raise HTTPException(status_code=503,
                                detail=f"assistant offline: {detail}")
        provider = app.state.provider

        def stream():
            session_id = req.session_id
            yield _sse("session", {"session_id": session_id})
            turns = list(app.state.sessions.get(session_id, []))
            outcome: Outcome | None = None
            log_id: int | None = None
            try:
                for event in engine.answer_stream(
                    req.question, provider, session_id=session_id,
                    registry=app.state.registry, session=turns,
                    few_shots=app.state.few_shots,
                ):
                    if event.kind == "token":
                        yield _sse("token", {"chunk": event.text})
                    elif event.kind == "sql":
                        yield _sse("sql", {"sql": event.sql, "rows": event.row_count,
                                           "truncated": event.truncated,
                                           "repairs": event.repairs})
                    elif event.kind == "outcome":
                        outcome = event.outcome
            except GeneratorExit:  # client left mid-stream: still durable below
                raise
            except Exception as exc:  # never a hang, never a crash: a logged error
                outcome = Outcome(error=f"engine failure: {type(exc).__name__}: {exc}")
            finally:
                if outcome is not None:
                    log_id = engine.log_exchange(
                        session_id, provider.name(), req.question, outcome)
                    answer_text = (outcome.text or outcome.refusal or outcome.error) or ""
                    app.state.sessions[session_id] = (
                        turns
                        + [{"role": "user", "content": req.question},
                           {"role": "assistant", "content": answer_text}]
                    )[-MAX_TURNS:]
            if outcome is not None:
                yield _sse("outcome", {
                    "outcome": engine.outcome_kind(outcome),
                    "text": outcome.text,
                    "detail": outcome.refusal or outcome.error,
                    "row_count": outcome.row_count,
                    "truncated": outcome.truncated,
                    "repairs": outcome.repairs,
                    "log_id": log_id,
                })

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/api/feedback")
    def feedback(req: FeedbackRequest) -> dict:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pipeline.chat_log SET thumbs = %s "
                    "WHERE id = %s AND thumbs IS NULL RETURNING id",
                    (req.thumbs, req.log_id),
                )
                rated = cur.fetchone()
        if rated is None:
            raise HTTPException(status_code=404,
                                detail="chat log row not found or already rated")
        return {"ok": True}

    return app