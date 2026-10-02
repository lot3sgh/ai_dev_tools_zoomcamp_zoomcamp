"""Agent extension pack — MCP server contract (backend tests).

The MCP tools are exercised at the protocol seam (handle_request with framed JSON-RPC)
against the fixture database with the deterministic stub provider: no network, no key.
A subprocess smoke test proves the stdio entry point boots and answers a frame.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from assistant.mcp import handle_request, TOOLS
from assistant_core import StubProvider
import pytest



os.environ.setdefault("LLM_PROVIDER", "stub")


def _rpc(method, params=None, message_id=1):
    return handle_request({"jsonrpc": "2.0", "method": method, "params": params or {},
                           "id": message_id})


# ---------------------------------------------------------------- handshake

def test_initialize_handshake():
    result = _rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "test", "version": "0"}})["result"]
    assert result["serverInfo"]["name"] == "health-assistant"
    assert "tools" in result["capabilities"]
    assert result["protocolVersion"]


def test_tools_list_exposes_the_capabilities():
    tools = _rpc("tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == ["ask_health", "semantic_layer_surface"]
    assert TOOLS[0]["inputSchema"]["required"] == ["question"]


def test_ping_and_unknown_methods():
    assert _rpc("ping")["result"] == {}
    error = _rpc("no/such/method")
    assert error["error"]["code"] == -32601


# ---------------------------------------------------------------- tool calls

def test_semantic_layer_surface_requires_no_database():
    result = _rpc("tools/call", {"name": "semantic_layer_surface", "arguments": {}})
    text = result["result"]["content"][0]["text"]
    assert "daily_health" in text and "bronze" in text.lower()
    assert result["result"]["isError"] is False


@pytest.mark.integration
def test_ask_health_answers_a_corpus_question_from_the_fixture(reset_db, source_dir, monkeypatch):
    """The engine runs for real (stub provider); the tool call returns the answer text."""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    from pipeline.cli import main

    assert main(["sync", "--source", "local", "--path", str(source_dir)]) == 0

    result = _rpc("tools/call", {
        "name": "ask_health",
        "arguments": {"question": "How many health days are in the database?",
                      "session_id": "mcp-test"},
    })
    body = result["result"]
    assert body["isError"] is False
    text = body["content"][0]["text"]
    assert text  # the stub phrases an answer after running the real SQL


def test_ask_health_requires_a_question():
    result = _rpc("tools/call", {"name": "ask_health", "arguments": {}})
    assert result["result"]["isError"] is True
    assert "question" in result["result"]["content"][0]["text"]


def test_unknown_tool_is_a_protocol_error():
    result = _rpc("tools/call", {"name": "nope", "arguments": {}})
    assert result["error"]["code"] == -32602


# ---------------------------------------------------------------- process smoke

def test_stdio_server_boots_and_answers_a_frame():
    """The real entry point: spawn mcp-server/server.py and exchange one RPC round."""
    env = dict(os.environ, LLM_PROVIDER="stub")
    proc = subprocess.Popen(
        [sys.executable, "mcp-server/server.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({
        "jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}) + "\n")
    proc.stdin.flush()
    response = json.loads(proc.stdout.readline())
    assert {t["name"] for t in response["result"]["tools"]} == \
           {"ask_health", "semantic_layer_surface"}
    proc.terminate()
    proc.wait(timeout=10)