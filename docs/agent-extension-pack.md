# Agent Extension Pack

How an agent (the coding agent building this repo, or an MCP client consuming it) is
armed: project instructions, reusable capabilities, guardrails, a specialist profile,
an MCP surface, and the permission notes. Everything here is real and tested — no
stub-in-docs claims.

## Contents

| Piece | Where | Status |
|---|---|---|
| Project instructions | `AGENTS.md` (+ vocabulary in `CONTEXT.md`) | live, read by agents on entry |
| Capability registry | `agent-capabilities/` — `run_health_query` registration + contract | live (`assistant.engine.register_health_tools`) |
| Guardrails (hooks) | `agent-hooks/` — the SQL-surface guard, enforced in `RunHealthQuery.run()` | enforced + unit/integration tested |
| Specialist agent | `custom-agent/` — role, permissions, working agreement | docs + config |
| MCP server | `mcp-server/` — `ask_health` + `semantic_layer_surface` over stdio | live, JSON-RPC tested + process smoke test |
| Permission notes | `docs/permissions.md` | mirrors the enforced model |
| AI workflow record | `docs/ai-workflow.md` | how AI tools built and reviewed this project |

## The capability loop (write once, gated everywhere)

1. A capability = one registered `Tool` on the core `Registry`.
2. Its model-facing semantics come from DB `COMMENT ON` text — versioned with the
   schema, never only in a prompt.
3. The eval corpus (25 hand-derived pairs) is the merge gate for any capability
   change — the same corpus seeds the tool's few-shots.
4. The guardrail intercepts before execution; the role grants enforce after.
5. MCP + HTTP expose the same engine, so every surface shares one behavior
   (tested at each seam).

## Adding a capability (v1 rule)

This repo registers exactly one tool. A second tool goes through: a few corpus pairs
(showing the model how to use it), a guard entry (if it reads SQL), the role grant
change, the Chat Log columns if the outcome shape changes — and a full `make eval-gate`
run. No tool may be documented before its corpus pairs pass.

## Testing the pack

```bash
make test            # uv run pytest -q (backend + agent-hooks + MCP protocol)
cd frontend && npm test    # node --test (SSE parser + API client)
uv run pipeline eval --self-check   # deterministic corpus self-check
make eval-gate       # provider gate (skips without LLM_API_KEY)
```