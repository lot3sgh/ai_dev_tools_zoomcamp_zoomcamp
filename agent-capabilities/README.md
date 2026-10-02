# Agent Capabilities

Capabilities are what agents (chat orchestrators, MCP clients, CI gate) can do with
this project. The runtime registry lives in the generic core
(`health-assistant-core/src/assistant_core/registry.py`); each registered `Tool`
carries `name` + `description` + `docs()` (model-facing semantics from DB comments)
+ `run()` (the execute contract).

## Registered capabilities

| Tool | What it does | Enforced by |
|---|---|---|
| `run_health_query(sql)` | Executes read-only SQL over the Semantic Layer | chatbot role grants (bronze unreachable) + `agent-hooks` SQL-surface guard + execute rails (10 s timeout, 500-row cap, EXPLAIN dry-run, ≤2 repairs) |

The registration seam (health side): `assistant.engine.register_health_tools(registry)`
adds exactly one tool to a capability `Registry` — see
[`agent-capabilities/run-health-query.md`](run-health-query.md).

## How a capability becomes available to an agent

1. An agent loop constructs a `Registry` (core) and calls the health registration
   (this repo) — the *composition seam* of ADR-0006.
2. The tool's `docs()` pulls the model-facing grain semantics from the database
   (`COMMENT ON VIEW`/`COMMENT ON COLUMN`), versioned with the schema.
3. The eval corpus (25 golden pairs) double-checks every data/refusal class the
   capability claims to serve — a capability change is a corpus change.
4. The MCP server (`mcp-server/`) exposes the same capability to MCP clients as
   `ask_health` / `semantic_layer_surface`.

## Scope rule

Capabilities never reach bronze or the high-volume silver families, never write, and
never execute outside the registered tool. Anything else is a refusal, not an answer.