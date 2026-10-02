"""MCP tool server for the Health Assistant (agent extension pack).

Exposes the assistant's capability as two MCP tools over stdio JSON-RPC (the spec's
newline-delimited transport):

- `ask_health(question, session_id?)` — runs the full engine (provider from env, the
  registered run_health_query tool with all its rails) and returns the answer, a
  refusal, or an error as tool output. Refusals are NOT failures (isError=false):
  "I won't answer that" is a legitimate result of the contract.
- `semantic_layer_surface()` — the documented SQL surface (what run_health_query may
  touch and what is unreachable).

No dependencies: everything is stdlib. The entry point is mcp-server/server.py
(`uv run python mcp-server/server.py`); this module holds the protocol logic so the
backend tests exercise it directly.
"""

from __future__ import annotations

import json
import sys

from agent_hooks.sql_surface_guard import surface_docs

PROTOCOL_VERSION = "2025-03-26"

SERVER_INFO = {"name": "health-assistant", "version": "0.3.0"}

TOOLS = [
    {
        "name": "ask_health",
        "description": (
            "Ask the Health Assistant a health-data question in plain language; it "
            "answers by writing and running read-only SQL over the Semantic Layer "
            "(gold views + sleep_score/device/profile) under a strict execute contract, "
            "then phrasing a natural-language answer. Returns the answer text, a "
            "refusal, or an error — never fabricated data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "the question, e.g. 'how was my sleep last night?'",
                },
                "session_id": {
                    "type": "string",
                    "description": "stable conversation id for follow-ups and provider routing",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "semantic_layer_surface",
        "description": "The documented read surface of run_health_query: which relations "
                       "are reachable and which are refused by construction.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def handle_request(message: dict) -> dict | None:
    """One JSON-RPC request -> one response (or None for notifications)."""
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "notifications/initialized":
        return None  # notification: no response
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return _tool_call(message_id, params)
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _tool_call(message_id, params) -> dict:
    name = (params.get("name") or "")
    arguments = params.get("arguments") or {}
    try:
        if name == "ask_health":
            text, is_error = _ask_health(arguments)
            return {"jsonrpc": "2.0", "id": message_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
        if name == "semantic_layer_surface":
            return {"jsonrpc": "2.0", "id": message_id,
                    "result": {"content": [{"type": "text", "text": surface_docs()}],
                               "isError": False}}
        return {"jsonrpc": "2.0", "id": message_id,
                "error": {"code": -32602, "message": f"unknown tool: {name}"}}
    except Exception as exc:  # noqa: BLE001 - the protocol must never hang a client
        return {"jsonrpc": "2.0", "id": message_id,
                "result": {"content": [{"type": "text",
                                        "text": f"engine failure: {type(exc).__name__}: {exc}"}],
                           "isError": True}}


def _ask_health(arguments: dict) -> tuple[str, bool]:
    question = str(arguments.get("question", "")).strip()
    if not question:
        return "ask_health requires a non-empty 'question' argument", True

    from assistant import engine, providers
    from pipeline import config

    config.load_env()
    provider = providers.build_provider()
    outcome = engine.answer_question(
        question,
        provider,
        session_id=str(arguments.get("session_id") or "mcp"),
    )
    if outcome.text is not None:
        return outcome.text, False
    if outcome.refusal is not None:
        return outcome.refusal, False  # refusal is a legitimate result, not an error
    return outcome.error or "engine failure", True


def serve() -> int:
    """Run the stdio loop (newline-delimited JSON-RPC). Called by mcp-server/server.py."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        try:
            response = handle_request(request)
        except Exception as exc:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32603, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0