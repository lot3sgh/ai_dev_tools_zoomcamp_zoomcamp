"""Phase 3 T3 — OpenAI-compatible provider adapter, at its real seam.

The adapter talks HTTP; the tests point it at a local stdlib stub gateway (no network,
no key) and assert on the provider boundary: streamed text, refusal semantics for
missing key / outage / bad status / malformed streams, and the wire contract (model id,
auth header, streaming request body).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assistant_core import Orchestrator, OpenAICompatProvider, StubProvider, Tool, ToolResult

MODEL = "opencode-go/deepseek-v4-flash"


def _sse(*contents: str) -> str:
    """A canned OpenAI-style SSE response body: one data frame per content delta."""
    frames = []
    for c in contents:
        frames.append("data: " + json.dumps(
            {"choices": [{"delta": {"content": c}}]}) + "\n\n")
    frames.append("data: [DONE]\n\n")
    return "".join(frames)


# ---------------------------------------------------------------- stub gateway

class _GatewaySession:
    """Canned per-request responses (last one repeats) + captured wire details."""

    responses: list[str] = []
    statuses: list[int] = []
    counter = 0
    last_body: dict | None = None
    last_auth: str | None = None


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        _GatewaySession.last_body = json.loads(payload or b"{}")
        _GatewaySession.last_auth = self.headers.get("Authorization")
        n = _GatewaySession.counter
        _GatewaySession.counter += 1
        status = _GatewaySession.statuses[min(n, len(_GatewaySession.statuses) - 1)]
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if status == 200:
            body = _GatewaySession.responses[min(n, len(_GatewaySession.responses) - 1)]
            self.wfile.write(body.encode())
        else:
            self.wfile.write(b'{"error": "boom"}')
        self.wfile.flush()

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture()
def gateway():
    _GatewaySession.responses = []
    _GatewaySession.statuses = [200]
    _GatewaySession.counter = 0
    _GatewaySession.last_body = None
    _GatewaySession.last_auth = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _url(server) -> str:
    return f"http://127.0.0.1:{server.server_port}/v1"


class _EchoTool(Tool):
    name = "echo"
    description = "echoes the SQL back"

    def docs(self, few_shots: str = "") -> str:
        return "echo schema: nothing"

    def run(self, sql: str) -> ToolResult:
        if "bad" in sql:
            return ToolResult(error="syntax error at or near 'bad'")
        return ToolResult(rows=[(sql,)], row_count=1)


# ---------------------------------------------------------------- wire contract

def test_wire_contract_sends_model_stream_and_bearer(gateway):
    _GatewaySession.responses = [_sse("hi")]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="secret-key")
    list(provider.stream([{"role": "user", "content": "q?"}]))
    assert _GatewaySession.last_body["model"] == MODEL
    assert _GatewaySession.last_body["stream"] is True
    assert _GatewaySession.last_body["messages"][-1]["content"] == "q?"
    assert _GatewaySession.last_auth == "Bearer secret-key"
    assert provider.name() == MODEL


def test_name_reports_provider_label_when_given(gateway):
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k",
                                    provider_name="ollama-local")
    assert provider.name() == "ollama-local"


# ---------------------------------------------------------------- streaming

def test_stream_relays_content_deltas_in_order(gateway):
    _GatewaySession.responses = [_sse("It", " was ", "a good night")]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k")
    pieces = list(provider.stream([{"role": "user", "content": "q?"}]))
    assert "".join(pieces) == "It was a good night"


def test_end_to_end_round_trip_against_gateway(gateway):
    """SQL action turn, then phrased answer turn — the whole loop on a real HTTP seam."""
    _GatewaySession.responses = [
        _sse("SQL:\nSELECT 1"),
        _sse("ANSWER:\n", "forty two"),
    ]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k")
    out = Orchestrator(provider, _EchoTool()).answer("q?")
    assert out.text == "forty two"
    assert out.sql == "SELECT 1"
    assert out.row_count == 1
    assert out.repairs == 0


# ---------------------------------------------------------------- refusal semantics

def test_missing_api_key_is_a_refusal_no_fallback(gateway):
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key=None)
    text = "".join(provider.stream([{"role": "user", "content": "q?"}]))
    assert text.startswith("REFUSE:")
    assert "LLM_API_KEY" in text


def test_http_error_status_is_a_refusal(gateway):
    _GatewaySession.statuses = [429]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k")
    text = "".join(provider.stream([{"role": "user", "content": "q?"}]))
    assert text.startswith("REFUSE:")
    assert "429" in text


def test_connection_refused_is_a_refusal(gateway):
    closed = _url(gateway)
    gateway.shutdown()
    gateway.server_close()
    provider = OpenAICompatProvider(closed, MODEL, api_key="k")
    text = "".join(provider.stream([{"role": "user", "content": "q?"}]))
    assert text.startswith("REFUSE:")
    assert "unreachable" in text


def test_malformed_first_frame_is_a_refusal(gateway):
    _GatewaySession.responses = ["data: not-json\n\n"]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k")
    text = "".join(provider.stream([{"role": "user", "content": "q?"}]))
    assert text.startswith("REFUSE:")
    assert "malformed" in text


def test_empty_delta_frames_are_skipped(gateway):
    _GatewaySession.responses = [_sse("", "x", "", "y")]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k")
    assert "".join(provider.stream([{}])) == "xy"


# ---------------------------------------------------------------- plumbing

def test_complete_matches_stream_text(gateway):
    _GatewaySession.responses = [_sse("A", "B")]
    provider = OpenAICompatProvider(_url(gateway), MODEL, api_key="k")
    assert provider.complete([{"role": "user", "content": "q?"}]) == "AB"


def test_endpoint_joins_gateway_path(gateway):
    """base_url may be root-only; /chat/completions is appended."""
    server_url = f"http://127.0.0.1:{gateway.server_port}"
    _GatewaySession.responses = [_sse("ok")]
    provider = OpenAICompatProvider(server_url, MODEL, api_key="k", chat_path="/v1/chat/completions")
    assert "".join(provider.stream([{}])) == "ok"