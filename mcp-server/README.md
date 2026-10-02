# MCP server

A Model Context Protocol (stdio) server exposing the Health Assistant as two tools,
with zero non-stdlib dependencies (the MCP stdio transport is newline-delimited
JSON-RPC, so `assistant.mcp` implements it directly).

## Run

```bash
# from the repository root (the uv venv is installed by `uv sync`)
uv run python mcp-server/server.py
```

Client configuration (any MCP host):

```jsonc
{
  "mcpServers": {
    "health-assistant": {
      "command": "uv",
      "args": ["run", "python", "mcp-server/server.py"],
      "cwd": "/absolute/path/to/repository"
    }
  }
}
```

## Tools

| Tool | returns |
|---|---|
| `ask_health(question, session_id?)` | the natural-language answer; or a refusal (health data out of surface / honestly unknown) as non-error text; or an error |
| `semantic_layer_surface()` | the documented read surface of `run_health_query` |

`ask_health` runs the same engine as the web app: provider from env (the
OpenAI-compatible seam; `LLM_PROVIDER=stub` is the deterministic demo), the registered
tool with all execute rails, and the Chat Log entry per exchange. Refusals are returned
as text (`isError: false`) — the contract treats "I won't answer that" as a legitimate
result, never as a crash.

## Protocol

`initialize` / `ping` / `tools/list` / `tools/call` / `notifications/initialized` are
supported. The handshake negotiates protocol `2025-03-26` and announces the `tools`
capability. Unknown methods and unknown tools get JSON-RPC errors (-32601 / -32602);
engine failures are returned inside the tool result (`isError: true`) so a client
never hangs.

## Tests

The backend suite drives the protocol directly at the JSON-RPC seam and smoke-tests the
real stdio process (`tests/test_mcp_server.py`): handshake, tools list, surface doc,
an actual fixture-database answer via the stub provider, error frames, and the process
boot.