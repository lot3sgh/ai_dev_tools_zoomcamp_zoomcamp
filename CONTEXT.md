# Health Data Pipeline

Ingests the user's Google Health (Fitbit) takeout archives from Google Drive into Postgres.

## Language

**Takeout**:
A zip archive produced by Google's export of the user's Google Health data (Google Takeout). The pipeline's unit of ingestion — one takeout = one archive.
_Avoid_: Export, zip file, backup

**Service Account (SA)**:
The Google identity whose credentials (`my-health-sp@wise-weaver-509208-b9`) the pipeline uses to authenticate to Google Drive. The takeout contents are Google's own export of this user's data, not the SA's.
_Avoid_: Service principal, bot account, key file

**Drive File**:
A single file listed in Google Drive (in practice, a takeout zip). Identified by file id + `modifiedTime`; the metadata that determines what to download.
_Avoid_: Drive object, document

**Family**:
A group of CSVs under one subfolder of the takeout that share a row shape (e.g. all `Active Zone Minutes - YYYY-MM-DD.csv` files). Each family becomes exactly one raw table; inward schema drift is absorbed by widening with nullable columns.
_Avoid_: Dataset, folder, metric group

**Bronze Table**:
A Postgres table in the `bronze` schema holding a family's rows verbatim — every column as TEXT, plus provenance columns (`_takeout`, `_source_file`, `_loaded_at`). No types, no business meaning; that happens in the silver layer.
_Avoid_: Landing table, staging table, raw table

**Silver Table**:
A typed Postgres table in the `silver` schema derived from a curated bronze family — 9 families today (sleep, active zone minutes, device, profile, stress, hrv, spo2, temperature, activity). Entity tables (sleep, AZM, device, profile, stress, spo2) upsert by natural key; high-frequency tables without a defensible key (hrv, temperature, activity) are in append mode, with rerun idempotency from delete-then-insert per source file. Rows that fail type conversion go to `silver.rejected_rows` with the reason instead of failing the run; rows deliberately left unmapped (`_Skip`, e.g. temperature per-sample rows) stay bronze-only.
_Avoid_: Curated table, gold table, model table

**Gold Table**:
The read contract layer (Phase 0b): four SQL views in the `gold` schema — `daily_health`, `sleep_summary`, `activity_trends`, `freshness` — served to Grafana (and any future consumer) through the read-only `dashboard` role. Deliberately views, not materialized tables: no mart tooling (no dbt; ADR-0004 reasoning). Night metrics key to the date of the night they belong to.
_Avoid_: Report, dashboard table

**Semantic Layer**:
The curated SQL surface exposed to conversational consumers (the chatbot; Grafana reads the same contract). Four gold views + a whitelist of small, stable-grain silver tables (`sleep_score`, `device`, `profile`); `freshness` is in scope but operational. Bronze and the high-volume silver families are never part of the layer — an LLM writing direct SQL must not be able to reach them.
_Avoid_: Dataset, views, query surface

**Health Assistant**:
The conversational application (Phase 3): FastAPI + SSE chat endpoint, tool-calling orchestrator with one tool (`run_health_query`), a single-file mobile-friendly web UI, deployed as its own service on the Linux host. It talks to the Semantic Layer through the read-only `chatbot` role and records every exchange in the Chat Log. The generic core lives in this monorepo at `health-assistant-core/` (its own subtree history); this repository registers the health tools.
_Avoid_: Chatbot (keep as the product category), bot, chat app

**run_health_query**:
The Health Assistant's only tool — text-to-SQL over the Semantic Layer (`gold` views + the whitelisted silver tables). The model writes the SQL; the execute contract (10s statement timeout, 500-row cap, EXPLAIN dry-run, two repair attempts, no writes by role construction) bounds it. Every execution is logged to the Chat Log.
_Avoid_: Query tool, SQL tool, health query

**Chat Log**:
`pipeline.chat_log` — one row per question: provider, generated SQL, result-row count, outcome (answered / refused / error), user thumbs. It is the audit trail for the privacy boundary, the seed for the eval corpus, and the refusal detector's input.
_Avoid_: Log file, transcript
**Eval Corpus**:
The golden set of (question → expected executed rows / expected outcome) pairs in `assistant/corpus.py` (25 pairs, hand-derived from the synthetic takeout fixture). Judged by executed result rows, order-insensitive; the merge gate is ≥90% execution accuracy and 100% on refusals (`make eval-gate`; deterministic stub check: `pipeline eval --self-check`). The corpus doubles as the few-shot bank in the system prompt and grows from Chat Log exchanges with thumbs.
_Avoid_: Test set, golden queries

**Provider seam**:
The OpenAI-compatible adapter boundary (`LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`): Ollama local, Bosch BMF, DeepSeek native, or OpenCode Go are one env block apart, each gated by its own corpus run before adoption. `LLM_PROVIDER=stub` is the deterministic dev provider. Hosted-by-default is an explicit, audited privacy boundary (question + result rows transit the provider; every exchange records the provider in the Chat Log); there is no silent local fallback — an outage or missing key is a refusal.
_Avoid_: LLM config, model setting
