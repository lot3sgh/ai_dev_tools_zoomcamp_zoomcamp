# Health Assistant — Google Health Takeout pipeline + conversational AI

**The problem.** A home-lab user owns ~11M rows of personal health data (Google Health
takeouts) landing in Postgres as bronze → silver → gold. Every health question ("how
was my sleep last night?", "which ISO week was I most active?") still ended in
hand-written SQL — the person who owns the data cannot ask it conversationally.

**The system.** A full-stack app, all on one home server:

- a **pipeline** that ingests Google Health takeouts (Drive or local) into Postgres
  bronze → silver → gold + Grafana;
- a **text-to-SQL health chatbot** (FastAPI + SSE) that answers plain-language
  questions by writing and executing read-only SQL over the **Semantic Layer**
  (gold views + whitelisted silver), under a strict execute contract, streamed to a
  single-page mobile UI;
- a **measured quality gate** (25-pair eval corpus, ≥90% executed-correct, 100%
  refusals) that blocks merges per provider;
- an **agent extension pack** (capability registry, enforced guardrails, MCP server,
  custom-agent profile) and full security/ops/CI documentation.

Expected behavior, in one question: *"how was my sleep last night?"* →
the model proposes SQL → the read-only `chatbot` role executes it bounded by rails
(10 s timeout, 500-row cap, EXPLAIN dry-run) → the answer streams token-by-token →
the exchange lands in `pipeline.chat_log` → you can 👍/👎 it. Out-of-surface
questions ("what is my blood pressure?") are refused, never invented.

## Architecture & technologies

```
frontend/ (HTML+CSS+JS, no build step, noddy-free)
   │  fetch + SSE   ── single origin ──
backend/src/assistant/ (FastAPI + SSE chat service, OpenAI-compatible provider seam)
   │  run_health_query (execute contract) + agent-hooks guard + Chat Log
backend/src/pipeline/ (bronze → silver → gold + Grafana sync; Postgres)
   │
Postgres 16 (docker): bronze/silver/gold schemas, chatbot + dashboard read-only roles
Grafana (docker): dashboards over gold
MCP server (mcp-server/) + agent pack: the same engine for agent clients
```

| Layer | Technology | Role |
|---|---|---|
| Frontend | vanilla ES modules, `node --test` | single-page mobile chat UI; centralized `api.js` client |
| Backend | Python 3.12, FastAPI, Uvicorn, psycopg 3 | SSE chat API + the pipeline CLI |
| LLM seam | OpenAI-compatible client (stdlib) | one config flip: OpenCode Go · Ollama · BMF · DeepSeek |
| Database | Postgres 16 (docker compose) | bronze/silver/gold + chat_log; roles enforce the surface |
| Containerization | Dockerfile (sync) + Dockerfile.chat + compose | full system on the LAN via `docker compose up` |
| CI/CD | GitHub Actions | tests + mypy + eval gate on merge; SSH deploy template |
| Agent pack | registry, hooks, MCP, custom agent | Module-5 deliverables (see docs/agent-extension-pack.md) |

## Quickstart (local dev)

```bash
docker compose up -d db                 # Postgres on 5433 (Grafana too: up -d)
uv sync --extra dev
make sync-local                         # ingest ./data/*.zip → bronze/silver/gold
# zero-key demo (fixture DB, deterministic "corpus" provider):
uv run pipeline eval --self-check       # builds the demo fixture DB
LLM_PROVIDER=stub PGDATABASE=health_pipeline_eval \
  uv run uvicorn assistant.server:create_app --factory --host 0.0.0.0 --port 8000
# real model (see .env.example for the LLM block + privacy statement):
uv run uvicorn assistant.server:create_app --factory --host 0.0.0.0 --port 8000
# open http://localhost:8000  (or http://<host>:8000 on the LAN)
```

## Test

```bash
uv run pytest -q                        # full suite: integration (throwaway Postgres) + unit
make test-unit                          # unit-only: -m "not integration"
cd frontend && npm test                 # SSE parser + API client (node --test)
uv run python -m mypy                   # typecheck
uv run pipeline eval --self-check       # deterministic corpus self-check (25 pairs)
make eval-gate                          # real-provider gate (skips without LLM_API_KEY)
```

## Deploy (home-lab Linux host, LAN-only by design)

One `docker compose` stack; chat is LAN-bound (no port-forward, no auth in v1 — the
home network is the boundary, and health data is why). See `docs/deploy.md` for the
runbook (build, bring-up, provider config + privacy statement, verify, backup),
`ops/diagnosis.md` for the health probes, and `.github/workflows/deploy.yml` for the
SSH deployment template (requires secrets).

## Repository map

```
README.md  product-spec.md  AGENTS.md          rubric entry points
openapi.yaml                                   the API contract (served at /openapi.yaml)
backend/            Python pipeline + assistant (+ src layout, see backend/README.md)
frontend/           the single-page UI (see frontend/README.md)
health-assistant-core/   generic chatbot core (own history, subtree)
agent-capabilities/ run_health_query capability + registration
agent-hooks/        enforced SQL-surface guardrail
custom-agent/       the health specialist agent profile
mcp-server/         stdio MCP server (ask_health, semantic_layer_surface)
security/           audit.md (real scans), policy.md (AI tool/data policy)
ops/                diagnosis.md (operational probes + outputs)
docs/               deploy, ai-workflow, permissions, agent-extension-pack, adr/
.github/workflows/  ci.yml (merge gates), deploy.yml (SSH deploy template)
deploy/  grafana/   timers/backups, dashboards
```

## How AI tools were used

Spec → tickets → TDD at pre-agreed seams → regular typecheck/tests → two-axis review →
eval gate. Full record: `docs/ai-workflow.md`; security artifacts: `security/audit.md`
(gitleaks/pip-audit/bandit with real outputs); extension pack: `docs/agent-extension-pack.md`.