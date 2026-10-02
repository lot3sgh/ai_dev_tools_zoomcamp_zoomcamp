# AGENTS.md — project instructions for AI agents

What an agent working in this repository needs: the domain language, the seams tests
hug, the rule of no-internals assertions, and the gates every change must pass.
`CONTEXT.md` holds the glossary (Semantic Layer, Health Assistant, run_health_query,
Chat Log, Eval Corpus, Provider seam) — read it first; test names and interfaces must
match that vocabulary.

## Layout (monorepo)

```
backend/src/         Python: pipeline (ingest → silver → gold) + assistant (chat service,
                     engine, tool, eval corpus, MCP protocol, provider factory)
frontend/            the single-page UI (no build step): index.html + css/ + js/
                     (app.js = DOM glue, api.js = sole backend client, sse.js = parser)
agent-capabilities/  the run_health_query capability contract + registration seam
agent-hooks/         agent_hooks.sql_surface_guard — enforced pre-execution guard
agent-hooks/agent_hooks/
custom-agent/        the health specialist agent profile
mcp-server/          stdio MCP server exposing ask_health + semantic_layer_surface
health-assistant-core/  the generic chatbot core (own pyproject/tests, subtree history)
security/  ops/  docs/  .github/workflows/  deploy/  grafana/
```

## Working rules

1. **Test at external seams only.** HTTP/SSE responses + resulting database state,
   CLI return codes + DB state, JSON-RPC frames, executed result rows. Never assert on
   orchestrator internals, never mock `assistant_core` internals. Tests need no
   network and no key (stub providers + a local stub gateway).
2. **TDD vertical slices.** Red test first at the agreed seam, then the minimum
   implementation, then `mypy`, then the single file, then the full suite.
3. **Typecheck + full suite before any commit.** `uv run python -m mypy`,
   `uv run pytest -q`, and adjust the corpus when the tool/surface changes
   (`uv run pipeline eval --self-check` must stay green; `make eval-gate` for
   provider-affecting changes).
4. **Secrets.** `.env` and `wise-weaver-*.json` are gitignored — never commit them,
   never echo them into prompts/logs/issues, never add a new credential file without
   a `.gitignore` line. `.env.example` carries only placeholders.
5. **Domain invariants.** A night keys to the date sleep started; duplicate HRV rows
   are real rows; `spo2.value` is primary over `average_value`; bronze is unreachable
   by consumers. The DB comments are the source of truth — keep them and the tool
   surface (`assistant/tool.py`, `agent_hooks`) in lockstep.
6. **The execute contract is not for negotiation.** 10 s timeout, 500-row cap,
   EXPLAIN dry-run, ≤2 repairs, refusal-not-answer on material errors, honest "no
   data for that range" for empty ranges. No silent provider fallback — ever.
7. **Corpus changes are behavior changes.** New question classes and thumbed Chat Log
   rows are how the corpus grows; adding a pair means adding a gated expectation.
8. **Frontend.** No build step, no framework, no external assets; all backend reads go
   through `frontend/js/api.js`; protocol parsing lives in `sse.js` (node-tested).
9. **CI mirrors local gates** (`.github/workflows/ci.yml`): backend suite + mypy on a
   Postgres service, frontend `node --test`, deterministic eval self-check, and the
   real-provider gate when the `LLM_API_KEY` secret exists.

## When the model changes semantics, change these together

`backend/src/assistant/tool.py` (surface) · `agent-hooks/agent_hooks/sql_surface_guard.py`
(guard mirror) · `backend/src/pipeline/db.py` (grants + COMMENT ON) · the corpus pairs
(`backend/src/assistant/corpus.py`) · this file if the seams change.