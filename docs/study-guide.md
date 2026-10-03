# Study guide — Health Assistant

How to understand the architecture quickly and start learning the codebase, in the
order that works. Time boxes are honest: ~30 min for the map, ~1–2 h for the first
hands-on loop, and the exercises ladder takes you from reading to extending.

---

## 1. The one-paragraph mental model

Three planes, one contract.

```
 DATA PLANE          pipeline sync (backend/src/pipeline)
   Google Health takeouts ──► bronze (raw TEXT, provenance) ──► silver (typed, keyed)
   ──► gold (4 read-only views)          |  READ CONTRACT: the Semantic Layer
                                          |  = gold.* + sleep_score/device/profile
 INTELLIGENCE PLANE  assistant (backend/src/assistant) + generic core
   question ──► orchestrator loop ──► run_health_query (guard → EXPLAIN → execute
   as the chatbot role under rails) ──► phrased answer ──► SSE ──► frontend
   every exchange ──► pipeline.chat_log     quality ──► eval corpus (merge gate)

 PRESENTATION        frontend/ (single page) · MCP server · CI gate · Grafana (Phase 0b)
```

Everything the *chatbot* may touch is decided once and enforced three ways:
role grants (`chatbot` SELECT-only), a tool-layer guard (`agent-hooks`), and the
eval corpus (the measured "done-when"). Privacy boundary: with a hosted provider,
question + result rows leave the LAN per exchange — audited in the Chat Log, no
silent fallback.

## 2. Painting order (start here, ~30–45 min)

| Order | Read | Why |
|---|---|---|
| 1 | `README.md` | the shape: problem, layers, quickstart, test/deploy |
| 2 | `product-spec.md` | the requirements in one page (users, FRs, out-of-scope) |
| 3 | `CONTEXT.md` | the vocabulary — test names and docs use these words exactly |
| 4 | `docs/adr/0006-…` | the two load-bearing decisions: execute/privacy contract, monorepo |
| 5 | `docs/permissions.md` | who can do what — the enforcement map |
| 6 | `docs/ai-workflow.md` + `security/audit.md` | how it was built and verified (what "done" means here) |

Then follow **one question end to end** (see §3) — that single trace unlocks the
whole codebase.

## 3. One question, end to end (the master trace)

1. `frontend/js/app.js` — on submit → `api.js` `ask()` → `POST /api/chat`.
2. `backend/src/assistant/server.py` — FastAPI route; streams SSE
   (`session`/`sql`/`token`/`outcome`) from `engine.answer_stream()`.
3. `backend/src/assistant/engine.py` — builds an `Orchestrator` with the registered
   tool; logs every exchange to `pipeline.chat_log` (returns the row id → thumbs).
4. `health-assistant-core/src/assistant_core/orchestrator.py` — the generic loop:
   provider turn → parse `SQL:/ANSWER:/REFUSE:` → on SQL, run the tool → on error,
   repair ≤2; on success, hand rows back to the provider to phrase.
5. `backend/src/assistant/tool.py` (`RunHealthQuery.run`) — the **execute contract**:
   guard (`agent-hooks`) → `EXPLAIN` dry-run + 1M-row cardinality guard → 10 s
   timeout, 32 MB `work_mem` → 500-row cap with disclosure.
6. `backend/src/pipeline/db.py` — the grants (`provision_chatbot_role`) and the
   `COMMENT ON` texts that ARE the model's schema knowledge.
7. `backend/src/assistant/providers.py` — `build_provider()`: `LLM_PROVIDER=stub`
   (deterministic demo) or the OpenAI-compatible seam
   (`health-assistant-core/.../openai_compat.py`: stdlib HTTP, streams tokens,
   distinct refusals for missing key / outage / bad status).

## 4. The map, file by file

**Pipeline (data plane)** — `backend/src/pipeline/`

| File | Role |
|---|---|
| `cli.py` | the `pipeline` CLI: `sync` and `eval` subcommands (also `python -m pipeline`) |
| `sources.py` / `drive.py` | local-folder and Google-Drive takeout sources |
| `ingest.py` | bronze: every CSV verbatim (TEXT), family per table, provenance columns, delete-then-insert per source file |
| `silver.py` | typed tables: keyed upsert or append-mode per family (see ADR-0005) |
| `db.py` | idempotent DDL: schemas, gold views, Chat Log, role provisioning, `COMMENT ON` |
| `state.py` / `runner.py` | processed-file ledger + orchestration; `config.py` env resolution |

**Assistant (intelligence plane)** — `backend/src/assistant/`

| File | Role |
|---|---|
| `server.py` | FastAPI app: `/api/chat` (SSE), `/api/feedback`, static frontend, `/openapi.yaml` |
| `engine.py` | health registration seam: `register_health_tools`, `answer_stream`, `log_exchange` |
| `tool.py` | `run_health_query` — the contract (guard, rails, Chat Log shape) |
| `providers.py` | the env seam: stub demo vs OpenAI-compatible, privacy comments |
| `corpus.py` | 25 golden pairs (hand-derived) — the few-shot bank AND the yardstick |
| `eval.py` | runner + gate thresholds (≥90% data, 100% refusals) |
| `fixtures.py` | the synthetic takeout (single source for tests and corpus) |
| `mcp.py` | MCP protocol logic (exposed by `mcp-server/`) |

**Generic core** — `health-assistant-core/src/assistant_core/` (dependency-free; the
module-5 "pod-able core", own subtree history)

| File | Role |
|---|---|
| `orchestrator.py` | the streaming loop (`answer_stream`, `StreamEvent`s) — the heart |
| `provider.py` / `openai_compat.py` | adapter seam; stdlib OpenAI-compatible client |
| `stub.py` | deterministic scripted provider for tests |
| `registry.py` / `actions.py` | capability registry + outcome/tool-result types |

**Guards & surfaces** — `agent-hooks/` (SQL-surface guard, enforced), `mcp-server/`
(stdio MCP: `ask_health`, `semantic_layer_surface`), `custom-agent/`,
`agent-capabilities/` (the `run_health_query` contract doc).

**Operations** — `docker-compose.yml` (db · grafana · sync · chat), `Dockerfile`
(sync) + `Dockerfile.chat`, `deploy/` (timers, backup, eval gate),
`.github/workflows/` (ci + deploy), `ops/` + `security/` (diagnosis + audit/policy).

## 5. Quick commands you'll live in

```bash
docker compose up -d db          # the only external dependency for tests
uv sync --extra dev              # install the project + core (editable)

make test                        # full suite: 79 backend (25 unit / 54 integration) + 11 frontend
make test-unit                   # no-database unit layer
make typecheck                   # mypy
make eval-gate                   # real-provider corpus gate (skips without LLM_API_KEY)
uv run pipeline eval --self-check  # deterministic corpus self-check (fixture DB, stub)

# demo, zero keys:
uv run pipeline eval --self-check            # builds the fixture demo DB
LLM_PROVIDER=stub PGDATABASE=health_pipeline_eval \
  uv run uvicorn assistant.server:create_app --factory --port 8000
curl -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"question":"How many health days are in the database?"}'
```

## 6. The learning ladder (each step changes something real)

1. **Read-only traces.** Run the suite; `git log --oneline` and pick a ticket commit
   to read (`Phase 3 T1…T5`); curl the SSE endpoint and watch token/sql/outcome.
2. **Observe the gates.** Break nothing: run `pipeline eval --self-check`, then look
   at `tests/test_corpus.py::test_stub_self_check_passes_every_pair` to see what
   "judged" means (executed rows, projection-insensitive).
3. **Add an eval pair.** Append a `CorpusPair` in `corpus.py` (hand-derive expected
   values from the fixture — run a sync, `SELECT` from gold), keep the drift-lock
   test green, then `pipeline eval --self-check`.
4. **Extend the surface.** The full checklist a capability change touches:
   `tool.py` (surface) ↔ `agent_hooks` (guard mirror) ↔ `db.py` (grants +
   `COMMENT ON`) ↔ corpus pairs ↔ `docs/permissions.md`. Try whitelisting
   `silver.hrv` end-to-end and watch the three enforcement layers.
5. **Swap the provider.** Point `LLM_PROVIDER`/`LLM_BASE_URL=…/v1` at Ollama local;
   gate it (`make eval-gate`) before adopting it.
6. **Speak MCP.** `uv run python mcp-server/server.py`, then call
   `tools/list`/`tools/call` over stdio (JSON-RPC lines) — or drive it from a client.
7. **Deploy.** Follow `docs/deploy.md` on the Linux host; verify with
   `ops/diagnosis.md` probes.

## 7. Gotchas that will otherwise eat an hour

- **Tests need Postgres** (`docker compose up -d db`, port 5433) and nothing else —
  no network, no key. The suite tests at external seams only; don't "help" by
  mocking `assistant_core` internals.
- **The `chatbot` role's password is cluster-wide.** Every sync re-provisions it
  from `CHATBOT_DB_PASSWORD` (tests and `.env` agree by resolving the config, so
  running pytest won't break the live service).
- **`.env` is the config** (gitignored; `.env.example` is the shape). Many
  interfaces that look hard-coded are really `config.*` readers.
- **The corpus is load-bearing everywhere**: few-shot bank (system prompt), gate,
  drift-lock test. A surface change without a corpus change is a broken promise.
- **OpenCode Go requires `x-opencode-session`** — already threaded through the
  provider seam; if you build a new client, send it or the gateway refuses (as it
  did once — the gate caught it).
- **The core is dependency-free by design**; its adapter tests use a local stub HTTP
  gateway. Don't add HTTP libs to it.

## 8. Deeper dives (when curious)

- The streaming protocol: `health-assistant-core/…/orchestrator.py` `_stream_turn`
  (why SQL is never token-streamed, how `ANSWER:`/`REFUSE:` prefix detection works).
- Grain semantics: the `COMMENT ON` texts in `db.py` are the product's "schema docs";
  the corpus questions are written against exactly those semantics.
- Scoring philosophy: `eval.py` `rows_match` (order- and projection-insensitive;
  extra rows ≠ extra columns).
- The audit trail: `security/audit.md` (real scans), `docs/ai-workflow.md` (how the
  AI-assisted loop ran, and how the gate caught a real provider bug).