# Roadmap — Health Takeout Pipeline

Future initiatives for hosting and consuming the health pipeline as a home-lab service
(Docker / K3S). Status uses task-list checkboxes; one checked box ≈ one PR-sized
increment. Phase order matters — Phase 0 through 2 build the read-and-monitor loop,
Phase 3+ adds AI interactivity on top of the same database.

## Operating model

The pipeline is **batch by nature**: Google Takeout only exports when a human asks, so the
loop is *new export lands in the Drive folder → scheduled sync ingests → gold views update →
dashboard / chatbot read the same Postgres*. All downstream consumers are read-only views
over the database — they add no risk to the existing bronze/silver engine, which is
already idempotent and ledger-driven (ADR-0003).

---

## Phase 0 — Dashboard first (zero new tooling)

Assumption: **Grafana** with the built-in Postgres datasource (Metabase/Superset work the
same). This is the earliest visible payoff.

- [ ] Define gold views (the contract every consumer reads):
  - `gold.daily_health` — per-day sleep score, HRV, resting HR, AZM, stress
  - `gold.sleep_summary` — nightly aggregates + rolling averages
  - `gold.activity_trends` — weekly AZM / activity totals
  - `gold.freshness` — last sync per source (`pipeline.processed_files`), `rejected_rows` count
- [ ] Grafana: Postgres datasource pointing at the pipeline DB (read-only role)
- [ ] Panels: sleep score 30-day, HRV trend, stress vs. activity; one "pipeline health" panel
      (freshness + rejections) — alert when stale or rejections spike
- [ ] Gold views get a short ADR note (the views ARE the mart; no dbt, see Guardrails)

**Done when:** a Grafana dashboard answers "what was my sleep like this month?" with no
new runtime installed beyond Grafana itself.

## Phase 1 — Autonomous ingestion

- [ ] `Dockerfile` for the CLI (slim python, `pipeline sync --source drive` as entrypoint)
- [ ] Scheduling: K3S `CronJob` (daily) **or** systemd timer over the existing compose
      stack — pick the simpler one first; single user, daily is plenty
- [ ] Nightly `pg_dump` backup, shipped off-box (personal health data is non-negotiable)
- [ ] Freshness alert wired up once `gold.freshness` exists

**Done when:** a new takeout dropped into the Drive folder is ingested without any manual
step, and the DB is restorable from backup.

## Phase 2 — K3S placement, with discipline

- [ ] Move only **stateless** workloads into the cluster: sync CronJob, Grafana, chat API
- [ ] Postgres: keep as host docker container (volume + nightly dumps) **or** give it a real
      PVC *and* a backup story in the same change — never move the DB before backups exist
- [ ] Document the runbook (restore procedure, how to re-export a Takeout)

**Done when:** the workload survives a node/container restart and a documented restore
drill succeeds.

## Phase 3 — Health chatbot (text-to-SQL, not RAG)

The data is structured; "how was my sleep yesterday?" is a query, not a retrieval task.

- [ ] Read-only DB role + view-only access for the chatbot
- [ ] Tool-calling orchestrator: one tool `run_health_query(sql)` over gold/silver views,
      few-shot examples in the system prompt
- [ ] **LLM provider abstraction behind an OpenAI-compatible client** (env config):
      Ollama (local, privacy default) ↔ hosted API (e.g. Bosch BMF) drop-in via config —
      decide the privacy trade-off explicitly before wiring anything external
- [ ] SSE streaming chat endpoint (FastAPI), minimal UI (web or chat client)

## Phase 4 — Pod-able chatbot core

Reuse across home-lab projects: the core stays generic, domain knowledge lives in tools.

- [ ] Core: orchestrator loop + chat API + a discovered list of "capability tools"
- [ ] This repo registers *health tools* (SQL tool + schema docs + example questions)
- [ ] Other home-lab projects (media index, energy logs, …) register their own tools
      without touching the core
- [ ] Publish as a reusable chart/container in the lab registry

---

## Decision guardrails (extend ADR-0004's reasoning)

- **No dbt.** The required rejection-routing (`silver.rejected_rows` = warn, keep running)
  and dynamic schema widening are anti-dbt-philosophy; views scale fine at this size.
- **No streaming / warehouse.** Data is batch; Postgres 16 aggregates ~11M rows trivially.
- **No Airflow/Prefect.** One CronJob beats a scheduler for a single-user nightly sync.
  ADR-0004's revisit trigger (a second source/destination, or team-scale governance) is the
  only thing that changes this; same trigger applies to dbt.
- **DuckDB stays analyst-side** (optional): it can query Postgres directly or read parquet
  exports — it is a tool layered on top, never a pipeline stage.

## Open questions

- [ ] Grafana vs. alternate dashboard tool (assumed Grafana)
- [ ] Postgres: host-docker vs in-cluster PVC (decided when Phase 2 starts)
- [ ] LLM provider default for the chatbot (privacy-first: local Ollama)
- [ ] Takeout import cadence: how often to re-export from Google (the only manual step)
- [ ] Bronze replace-vs-accumulate semantics for same-path files across takeouts
      (`DELETE ... WHERE _source_file = %s AND _takeout = %s`) — decide before takeouts
      accumulate (raised live during first Drive sync)

## References

- `docs/SPEC.md` — behavioral contract (gold explicitly deferred there; Phase 0 un-defers it)
- `docs/adr/0001–0003` — sources, bronze-as-text, ledger decisions
- `docs/adr/0004-dlt-not-adopted.md` — framework-evaluation precedent this roadmap extends
- `CONTEXT.md` — glossary (Takeout, Service Account, Family, Bronze/Silver/Gold)