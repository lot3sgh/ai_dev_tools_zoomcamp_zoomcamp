# Spec: Google Health Takeout → Postgres Pipeline

Status: ready-for-agent
ADRs in force: [0001-drive-takeout-via-service-account](./adr/0001-drive-takeout-via-service-account.md), [0002-bronze-as-text-landing](./adr/0002-bronze-as-text-landing.md), [0003-pipeline-state-in-postgres](./adr/0003-pipeline-state-in-postgres.md)
Glossary: [CONTEXT.md](../../CONTEXT.md)

## Problem Statement

The user's Google Health (Fitbit) data is trapped in Google Takeout exports — large zip archives that accumulate over time (~3400 CSVs, monthly splits per metric). Today it sits as a static 1.4 GB sample file locally, with no way to query it, no automatic ingestion when a new takeout arrives, and no typed, analyzable form. The user wants a pipeline that extracts takeouts from Google Drive and lands them in a local Postgres database, structured so the data can actually be analyzed.

## Solution

A Python CLI pipeline that: authenticates to Google Drive as the project's Service Account (or reads a local directory of takeout zips — same engine), detects new or changed takeouts by md5, streams each takeout's CSVs into Postgres **bronze** tables (verbatim, TEXT, family-per-table, with provenance), and derives **silver** tables (typed, keyed) for the prime domains: sleep score, active zone minutes, device, profile. Rows that fail type conversion go to an audit rejection table rather than failing the run. The pipeline runs against a dockerized Postgres 16 on host port 5433, tracks every processed file in a state ledger, and is idempotent and re-runnable. Gold (aggregation) is explicitly deferred.

## User Stories

1. As the data owner, I want the pipeline to read takeout zips from Google Drive using the Service Account, so that new exports are ingested without me downloading anything.
2. As the data owner, I want the pipeline to also run against a local directory of takeout zips, so that development and offline runs need no Drive access.
3. As the data owner, I want drive, local, and Drive sources to share the same sync engine, so that behavior is identical regardless of source mode.
4. As the data owner, I want the pipeline to skip takeouts it has already processed, so that re-running does not duplicate rows.
5. As the data owner, I want the pipeline to detect changed takeouts by content (md5), so that an updated export is re-ingested even if the filename is unchanged.
6. As the data owner, I want the pipeline to record every Drive file it has seen (id, name, modified time, md5, status, row count), so that ingestion history is inspectable with SQL.
7. As the data owner, I want a failed takeout to be retried on the next run, so that transient errors recover without manual intervention.
8. As the data owner, I want deleting a file from Drive to leave the data in Postgres untouched, so that history is never destroyed by source cleanup.
9. As the data owner, I want every CSV in the takeout to land in Postgres verbatim, so that no data is silently dropped.
10. As the data owner, I want each takeout subfolder to become one bronze table, so that monthly files of the same metric are queryable together.
11. As the data owner, I want bronze rows to carry provenance (`_takeout`, `_source_file`, `_loaded_at`), so that every value can be traced to its source file.
12. As the data owner, I want schema drift between monthly files (new/renamed columns) to be absorbed without breaking the load, so that future takeouts with different shapes still land.
13. As the data analyst, I want a typed, keyed silver table for nightly sleep scores, so that sleep trends are queryable with real types.
14. As the data analyst, I want a typed, keyed silver table for active zone minutes (timestamp + heart zone), so that zone time can be aggregated.
15. As the data analyst, I want a typed silver table of paired devices, so that sensor/device data is queryable.
16. As the data analyst, I want a typed silver profile table, so that profile attributes are available for context.
17. As the data analyst, I want unparseable rows to land in a rejection audit table with the reason, so that no row silently vanishes during typing.
18. As the data analyst, I want UTC timestamps (`timestamptz`) everywhere in silver, so that times are comparable regardless of the profile's local timezone.
19. As the data owner, I want the pipeline to report per-run summary counts (bronze rows, silver upserts/rejects, files processed/skipped/failed), so that I can spot anomalies.
20. As the data owner, I want the run to exit non-zero on real errors but succeed with warnings on rejected rows, so that CI/automation fails loudly only on genuine failures.
21. As the data owner, I want the database schema to be created idempotently by the pipeline itself, so that a fresh environment comes up with one command.
22. As the developer, I want the Postgres to run via docker compose with a named volume, so that data survives restarts.
23. As the developer, I want the DB credentials loaded from a gitignored environment file, so that no secrets are committed.
24. As the developer, I want the Service Account key file gitignored, so that credentials are never committed.
25. As the developer, I want the vendored SDK and sample archive gitignored, so that the repository tracks only project code and docs.
26. As the developer, I want the project on Python ≥3.12 managed by uv, so that the local environment matches a real Python runtime that exists.
27. As the developer, I want a Makefile surface (`make up`, `make sync-local`, `make sync-drive`), so that common operations are one word.
28. As the developer, I want the CLI to expose a file limit option for dev, so that runs can be scoped during development.
29. As the developer, I want an end-to-end test that runs the CLI against a small synthetic takeout fixture and a throwaway Postgres, so that the whole engine is verified through one seam.
30. As the developer, I want a fixture that includes a deliberately bad row, so that the rejection-table behavior is tested.
31. As the developer, I want a re-run in the tests, so that idempotency and md5 no-op behavior are verified.
32. As the data owner, I want the gold (aggregation) layer explicitly absent, so that scope is clear and future work has a named destination.

## Implementation Decisions

- **Pipeline shape**: a Python package exposing a CLI. Modules by role: a Drive source adapter (Drive API listing + download, authenticated as the Service Account with `drive.readonly` scope), a local source adapter (directory of zips; file id = filename, md5 = file hash), a shared sync engine (file diff → process → record), an ingest step (zip → bronze), a silver build step (bronze → silver + rejected), a state ledger, a DB layer with idempotent DDL, and the CLI entrypoint.
- **Source contract**: both adapters emit the same per-file catalog — file id, name, modified time, md5, size — so the engine is identical for drive and local modes (ADR-0001).
- **Sync rule**: a file is processed when its id is unknown to the ledger or its md5 differs from the last processed run; failed files (status = error) are re-picked-up; deletions from the source are ignored (ADR-0003). State lives in `pipeline.processed_files` (file id, name, modified time, md5, status, row count, error message, processed at).
- **Bronze**: one table per family (takeout subfolder), every CSV value as TEXT, plus `_takeout`, `_source_file`, `_loaded_at`. Monthly files of a family merge; inward schema drift widens the table with nullable columns; egregiously incompatible siblings are coerced into the same family table and remain traceable by `_source_file` (ADR-0002). Loading is delete-then-insert scoped to the source file for idempotent re-runs.
- **Silver**: typed, keyed tables for the four prime domains — sleep score (natural key `sleep_log_entry_id`), active zone minutes (`date_time` + `heart_zone_id`), device (`wire_id`), profile (`id`). All timestamps are `timestamptz` (UTC); empty CSV cells become NULL; upsert on natural key. Rows that cannot be typed (bad date, non-numeric) go to `silver.rejected_rows` (reason + `_source_file`) and are reported as warnings, not failures.
- **Schema**: three schemas — `bronze`, `silver`, `pipeline` (state). DDL is idempotent and owned by the pipeline; no migration tool until silver stabilizes.
- **Runtime & tooling**: Python ≥3.12 (was pinned to unavailable 3.14), uv-managed environment; dependencies are the Google API client libraries (auth + Drive), the Postgres driver, and dev extras for testing.
- **Deployment**: docker compose with Postgres 16, host port 5433 (5432 is occupied in this environment), named volume, healthcheck; credentials from a gitignored environment file; Service Account key path from an environment variable, defaulting to the repo root for development.
- **CLI contract**: sync command with source mode (drive | local), optional path for local mode, optional limit for dev; clean summary output per run; exit code zero on success, non-zero on errors, success-with-warnings when only rows were rejected.

## Testing Decisions

- **Good test**: exercises external behavior only — run the pipeline, inspect the resulting database. No mocking of the sync engine's internals; no implementation assertions.
- **One seam**: the CLI end-to-end (ADR/spec-aligned). Tests run the sync command against a synthetic takeout fixture and a throwaway Postgres instance (docker), then assert on bronze rows, silver rows, rejection rows, and the state ledger.
- **Fixture**: a small synthetic takeout zip covering at least the sleep, active zone minutes, device, and profile families; includes one deliberately unparseable row (rejection path) and is sized for fast runs. The real 1.4 GB sample archive is not a test input.
- **Covered behaviors**: first run loads everything; re-run is a no-op (md5 unchanged); modified fixture content is re-ingested; rejected rows land with reasons; state ledger contains per-file status and row counts; bronze provenance columns are populated.
- **Prior art**: none — greenfield repository; this establishes the pattern. Pure helpers (family inference, diff rule) are exercised through the CLI seam via fixtures rather than separate unit seams.

## Out of Scope

- Gold/aggregation tables (daily totals, trends) — deferred, named as the future `gold` layer.
- Scheduling/orchestration (cron, Airflow) — the pipeline is an on-demand CLI; triggering is the operator's.
- Analytics connections (BI tools, notebooks, dashboards).
- Google Drive folder setup — enabling Drive API and sharing the folder with the Service Account is an operator action outside the pipeline.
- Migration tooling — until silver stabilizes.
- Data quality backfills or historical corrections.

## Further Notes

- Drive access is not yet verified end-to-end: the Service Account needs the target folder shared with it and the Drive API enabled; the pipeline requests its own `drive.readonly` token (default gcloud tokens lack Drive scope). Local mode is the fully testable path and is the primary development seam.
- The sample takeout archive is a dev fixture only, gitignored; it is not the source of truth for tests.
- Repo docs produced during design: CONTEXT.md glossary (Takeout, Service Account, Drive File, Family, Bronze Table, Silver Table, Gold Table) and three ADRs covering source architecture, TEXT bronze landing, and Postgres state.