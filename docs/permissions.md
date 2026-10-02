# Permissions model

What every actor in this system can and cannot do. The assistant runs with the least
privilege that still answers questions; every boundary below is enforced, not prompted.

## Actors and their database rights

| Actor | DB role | Select | Insert/Update/Delete |
|---|---|---|---|
| Health Assistant (run_health_query) | `chatbot` | Semantic Layer only: `gold.*` (4 views) + `silver.sleep_score/device/profile` | none — denied by grants |
| Grafana | `dashboard` | `gold` + all `silver` | none |
| Pipeline (sync/backup/timers) | `pipeline` (owner) | all schemas | gold/silver/pipeline DDL + data (its own role) |
| Web service (Chat Log + thumbs) | `pipeline` via `db.connect()` | scope limited by code | `pipeline.chat_log` only (service path) |

- **Bronze is unreachable by construction**: no role granted to it for consumers.
  Even *naming* `bronze.x` is a permission error (tested), and the agent guard
  refuses it before execution.
- Role passwords come from the environment and are provisioned idempotently on every
  sync (`ensure_schemas`): `DASHBOARD_DB_PASSWORD`, `CHATBOT_DB_PASSWORD`.

## The execute contract (per question)

1. **Tool-only SQL**: the model never has a database handle; it proposes SQL to
   `run_health_query`.
2. **Guard** (`agent-hooks`): multi-statement, non-read, or out-of-surface SQL →
   refusal before the dry-run.
3. **Dry-run**: `EXPLAIN` catches syntax/column errors; an estimated scan above 1M
   rows → refusal (forces a bounded `WHERE`).
4. **Rails**: 10 s statement timeout, `work_mem` 32 MB, 500-row cap (disclosed when
   truncated).
5. **Repair band**: ≤2 error-feedback rounds, then a refusal.
6. **Audit**: every exchange → `pipeline.chat_log` (provider, SQL, row count,
   outcome, thumbs).

## What the LLM provider can see (privacy boundary)

- The question and the result rows of each executed query transit the provider when a
  hosted gateway is configured (default: OpenCode Go). The Chat Log names the provider
  per exchange; there is **no silent fallback** — an outage or missing key is a
  refusal.
- Ollama local (or any local gateway) is a one-config flip; each provider must pass
  the corpus gate before adoption. See `security/policy.md`.

## Frontend / MCP / CI

| Surface | Rights |
|---|---|
| Web page (LAN) | None to the database; talks only to `/api/chat` + `/api/feedback` |
| `/api/feedback` | One thumbs rating per Chat Log row (idempotent, `thumbs IS NULL`) |
| MCP `ask_health` | Same engine as the web: tool rails + Chat Log; no new rights |
| CI | Tests + typecheck + corpus self-check; the provider gate only when the
  `LLM_API_KEY` secret exists (repository secrets on GitHub) |
| Deploy workflow | SSH to the home host; runs `make chat` + `make eval-gate` + `make chat-up` |

## Failed-closed invariants

- A config error (no provider, no key, no base URL) is a visible refusal/503 state,
  never a crash and never a silent different provider.
- A permission surprise (e.g. a leaked grant) is still refused by the tool guard and
  the EXPLAIN path; a tool-layer refusal never touches the executor twice.
- Contradictory prompts (jailbreaks) cannot turn the assistant into a writer: the
  role has no INSERT path, and the guard blocks the statement class outright.