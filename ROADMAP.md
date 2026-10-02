# Roadmap — Health Takeout Pipeline

Future initiatives for hosting and consuming the health pipeline as a home-lab service
(Docker / K3S). Status uses task-list checkboxes; one checked box ≈ one PR-sized
increment. Phase order matters — Phases 0a–2 build the read-and-monitor loop,
Phase 3+ adds AI interactivity on top of the same database.

## Operating model

The pipeline is **batch by nature**: Google Takeout only exports when a human asks, so the
loop is *new export lands in the Drive folder → scheduled sync ingests → gold views update →
dashboard / chatbot read the same Postgres*. All downstream consumers are read-only views
over the database — they add no risk to the existing bronze/silver engine, which is
already idempotent and ledger-driven (ADR-0003).

---

## Phase 0a — Silver expansion (Tier 1)

> ✅ Complete (2026-10-01): nine silver tables live (`95c13da`…`8766328`), gold views built on them, rejection counts visible via `gold.freshness`.

Prerequisite to the gold views: the SPEC's four prime tables were the minimal set, and the
dashboard/report contract needs these five typed, keyed tables. Mappings verified against
live bronze schemas (probe 2026-10-01): intraday heart rate lives inside
`physical_activity_googledata` (`beats_per_minute`); the `heart_rate` family is empty.

- [x] Add silver tables + row mappers (pattern: `silver.py` `_sleep`/`_azm` mappers) — all five shipped (`95c13da`, `d1fef31`, `ae06c38`, `4a3d908`, `8766328`):
  - `silver.stress` ← `stress_score` — key `date`; carries sleep/responsiveness/exertion
    points + status
  - `silver.hrv` ← `heart_rate_variability` — key: night/date; rmssd, LF/HF, coverage
  - `silver.spo2` ← `oxygen_saturation_spo2` — key `timestamp`; define precedence for
    `average_value` vs `value` (sample rows show one populated, other NULL)
  - `silver.temperature` ← `temperature` — key `(type, sleep_start)`; `nightly_temperature`
    + sample statistics
  - `silver.activity` ← `physical_activity_googledata` — **design question first**
    (intraday, 9.9M rows, no clean natural key): composite-key upsert
    `(timestamp, data_source)` vs typed-append with delete-by-`_source_file` (bronze-level
    idempotency already exists); consider monthly partitioning if Grafana queries lag
- [x] Watch `silver.rejected_rows` per new family — new rejection classes are expected and
      are the designed warning channel — done; live audit shows 3 classes (58,882
      `not an integer` in activity, 244 `not a date` in stress, 4 `missing wire_id` in
      paired_devices) surfaced via `gold.freshness` and the CLI run summary
- [x] ADR note on the promotion rule: promote when reports ask for typed/keyed values AND
      the family has a defensible key and clean time grain; everything else stays
      bronze-only (queryable via views) — same reasoning as ADR-0004 — shipped as
      `docs/adr/0005-gold-views-and-promotion-rule.md`

**Done when:** gold views can be built against typed silver tables for stress, HRV, SpO2,
temperature and intraday activity — with rejection counts visible in `gold.freshness`.

---

## Phase 0b — Dashboard first (zero new tooling)

> ✅ Complete (2026-10-01): gold views (gold.daily_health / sleep_summary / activity_trends / freshness, `376492c`), Grafana datasource + 4-panel dashboard + stale-sync alert (`eca6182`, `9670913`); dashboard answers the "how was my sleep this month?" question.

Assumption: **Grafana** with the built-in Postgres datasource (Metabase/Superset work the
same). This is the earliest visible payoff.

- [x] Define gold views (the contract every consumer reads) — all four shipped (`376492c`):
  - `gold.daily_health` — per-day sleep score, HRV, resting HR, AZM, stress
  - `gold.sleep_summary` — nightly aggregates + rolling averages
  - `gold.activity_trends` — weekly AZM / activity totals
  - `gold.freshness` — last sync per source (`pipeline.processed_files`), `rejected_rows` count
- [x] Grafana: Postgres datasource pointing at the pipeline DB (read-only role) — wired
      (`eca6182`; `grafana/provisioning/datasources/datasources.yml`, `dashboard` role from `04337aa`)
- [x] Panels: sleep score 30-day, HRV trend, stress vs. activity; one "pipeline health" panel
      (freshness + rejections) — alert when stale or rejections spike — 4 panels live in
      `grafana/dashboards/health.json` (Sleep 30d, Weekly steps, HRV, Pipeline freshness;
      render fixed in `9670913`); stale-sync alert wired, but the **rejections-spike alert is
      still pending**
- [x] Gold views get a short ADR note (the views ARE the mart; no dbt, see Guardrails) — shipped
      as `docs/adr/0005-gold-views-and-promotion-rule.md`; the runbook is `docs/dashboard.md`

**Done when:** a Grafana dashboard answers "what was my sleep like this month?" with no
new runtime installed beyond Grafana itself.

## Phase 1 — Autonomous ingestion

> ⚠ Target: the Linux deployment host (`docs/deploy.md`). Nothing is installed on the development Mac by design.

- [x] `Dockerfile` for the CLI (slim python, `pipeline sync --source drive` as entrypoint) — shipped with
      pinned hashed deps (`requirements.txt` from `uv export`) and a compose `sync` service
      (`profiles: ["sync"]`); image builds and the containerized run path are smoke-tested locally
- [ ] Scheduling: K3S `CronJob` (daily) **or** systemd timer over the existing compose
      stack — pick the simpler one first; single user, daily is plenty — systemd timers chosen and
      written (`deploy/health-sync.{service,timer}`, daily 06:15; `deploy/health-backup.*`, 03:10;
      `make install-timers` on the host). Not yet runtime-verified on the Linux box; K3S CronJob
      only arrives at Phase 2 placement
- [ ] Nightly `pg_dump` backup, shipped off-box (personal health data is non-negotiable) —
      `deploy/backup.sh` written (dump → gzip → retention → optional rclone/rsync off-box); pick an
      off-box target before first deploy
- [x] Freshness alert wired up once `gold.freshness` exists — `grafana/provisioning/alerting/stale-sync.yaml`
      (fired when `MAX(processed_at)` is older than 5 days; note: with an empty ledger the
      alert currently sees NoData — a scheduled sync heals the ledger)

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

> 📄 Spec & tickets: `.scratch/phase-3-health-assistant/issues/` (00-spec-phase-3.md + tickets 01–05,
> **complete** — 01 committed, 02–05 committed with the work below). Locked decisions: direct
> text-to-SQL over the **Semantic Layer**
> (gold views + sleep_score/device/profile; bronze unreachable by role); dedicated `chatbot` role
> with an execute contract (10 s timeout, 500-row cap, EXPLAIN dry-run, ≤2 repairs, Chat Log);
> provider = OpenCode Go / `opencode-go/deepseek-v4-flash` via an OpenAI-compatible env seam,
> no silent fallback; eval-corpus gate (25 pairs; ≥90% executed-correct, 100% refusals) as the
> merge gate — `make eval-gate`; single-page SSE web UI on the LAN (port 8000, no auth in v1);
> generic core in this monorepo (`health-assistant-core/`; monorepo per ADR-0006 addendum). ADR-0006
> (two-repo architecture + execute/privacy contract, consolidated to a monorepo 2026-10-02)
> accompanies the spec. First real-provider adoption (OpenCode Go / Ollama / BMF) still needs its
> own corpus gate run before use.

- [x] Read-only DB role + view-only access for the chatbot
- [x] Tool-calling orchestrator: one tool `run_health_query(sql)` over gold/silver views,
      few-shot examples in the system prompt (the eval corpus doubles as the few-shot bank)
- [x] **LLM provider abstraction behind an OpenAI-compatible client** (env config):
      Ollama (local, privacy default) ↔ hosted API (e.g. Bosch BMF) drop-in via config —
      privacy trade-off decided explicitly (ADR-0006): hosted is the default, audited per
      exchange in the Chat Log, no silent fallback
- [x] SSE streaming chat endpoint (FastAPI), minimal UI (web or chat client)

## Phase 4 — Pod-able chatbot core

Reuse across home-lab projects: the core stays generic, domain knowledge lives in tools.

> Status: the core's loop, provider-adapter seam, tools registry and streaming events exist
> (health-assistant-core, built in Phase 3; monorepo — its own subtree history in this checkout); “chat API” deliberately
> lives on the health side (ADR-0006). Remaining Phase-4 work: a capability *discovery*
> convention, a second registered tool, and a published container/chart in the lab registry.

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
- [ ] `silver.activity` intraday keying: composite-key upsert vs typed-append +
      delete-by-`_source_file` (decided in Phase 0a)

## References

- `docs/SPEC.md` — behavioral contract (gold explicitly deferred there; Phase 0 un-defers it)
- `docs/adr/0001–0003` — sources, bronze-as-text, ledger decisions
- `docs/adr/0004-dlt-not-adopted.md` — framework-evaluation precedent this roadmap extends
- `CONTEXT.md` — glossary (Takeout, Service Account, Family, Bronze/Silver/Gold)