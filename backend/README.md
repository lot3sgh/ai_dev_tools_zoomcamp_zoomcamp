# Backend

The Python side of the monorepo (packages declared in `pyproject.toml`; `uv sync`
installs them editable).

```
backend/src/pipeline/     the data pipeline: bronze (raw takeout text) → silver (typed,
                          keyed tables) → gold (read views) + state, Drive source, CLI
backend/src/pipeline/db.py     idempotent DDL + read-only role provisioning
                                (chatbot/dashboard) + Semantic Layer COMMENT ON docs
backend/src/assistant/    the Health Assistant: FastAPI/SSE service (server.py), the
                          engine + Chat Log (engine.py), run_health_query (tool.py),
                          eval corpus (corpus.py) + gate runner (eval.py), provider
                          factory (providers.py), MCP protocol (mcp.py)
agent-hooks/              agent_hooks.sql_surface_guard — enforced pre-execution
                          guardrail imported by assistant.tool
```

Three processes run this code:

1. **`pipeline sync`** (containerized `sync` image) — ingest, provisioned roles.
2. **The chat service** (`Dockerfile.chat` → uvicorn `assistant.server:create_app`
   `--factory`) — serves the UI + `/api/chat` (SSE) + `/api/feedback` + `openapi.yaml`.
3. **`pipeline eval`** (`--self-check` deterministic; default = provider gate) — the
   merge gate; also `mcp-server/server.py` (MCP stdio) runs the same engine.

The API contract is `openapi.yaml` at the repo root (also served by the app); the
service's endpoints mirror it and its Pydantic models validate the same shapes. Tests
(`tests/`) drive every layer through an external seam: HTTP/SSE responses + resulting
database state, CLI return codes + DB state, JSON-RPC frames, executed result rows —
never orchestrator internals.