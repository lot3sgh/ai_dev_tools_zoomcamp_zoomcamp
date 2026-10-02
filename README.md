# Google Health Takeout → Postgres Pipeline

Ingests Google Health (Fitbit) takeout archives from Google Drive (or a local folder of zips) into Postgres with a **bronze → silver → gold + Grafana** stack. Every CSV lands verbatim in bronze (TEXT, family-per-table, with provenance); 9 curated families are typed into silver (entities upsert by natural key; high-frequency families append idempotently); gold views are the read contract (daily health, sleep summary, weekly trends, freshness) served to Grafana through a read-only `dashboard` role. See `docs/SPEC.md` for the spec, `docs/adr/` for the architecture decisions, and `ROADMAP.md` for what's next.

## Quickstart

```bash
# 1. Bring up the stack: Postgres (docker compose; host port 5433, DB creds from .env)
#    and Grafana (host port 3000, see .env.example for its login vars)
docker compose up -d       # or: make up  (db only)

# 2. Install deps (uv, Python >= 3.12)
uv sync --extra dev

# 3. Sync the local sample archive (data/*.zip) into bronze + silver + gold
make sync-local

# 4. Query it
docker exec -it health-pipeline-postgres psql -U pipeline -d health_pipeline

# 5. Or look at it: http://localhost:3000 (Grafana, logs in with GRAFANA_ADMIN_*)
```

## Commands

| Command | What it does |
|---|---|
| `docker compose up -d` | start the full stack: Postgres 16 (:5433) + Grafana (:3000, waits for a healthy DB) |
| `make up` / `make down` | start / stop the Postgres container only |
| `make sync-local` | sync takeout zips from `./data` |
| `make sync-drive` | sync takeout zips from Google Drive |
| `make test` | 28-test E2E suite (synthetic fixture, throwaway Postgres) |
| `make typecheck` | mypy over the package |
| `make image` / `make sync-drive-container` | build / run the containerized sync runner (Linux deployment) |
| `make backup` | take a `pg_dump` now via `deploy/backup.sh` |
| `make install-timers` | install the daily sync + nightly backup systemd timers (**Linux host only**) |

Direct CLI: `uv run pipeline sync --source local --path <dir>` / `--source drive`, with `--limit N` to scope a dev run.

Linux deployment (the target for automation): see `docs/deploy.md` — the same code runs as a
docker container via the compose `sync` service, scheduled by systemd timers, with nightly
off-box `pg_dump` backups. Nothing is installed on the development Mac by design.

## Environment

Credentials live in `.env` (gitignored; `.env.example` shows the shape):

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `PGHOST`, `PGPORT` — database
- `GOOGLE_APPLICATION_CREDENTIALS` — Service Account key for Drive mode (optional for local mode; defaults to the key at repo root)
- `DASHBOARD_DB_PASSWORD` — read-only analytics login (Grafana); the pipeline provisions the `dashboard` role idempotently on every sync when set
- `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` — Grafana's own login (defaults: `admin` / `change_me_grafana`)

## Google Drive mode

The pipeline authenticates as the project's Service Account with a `drive.readonly`-scoped token and treats each file it finds as a takeout archive (same engine as local mode, md5-based change detection).

Two external preconditions, done once by an operator:

1. Enable the **Drive API** on the project the Service Account belongs to.
2. Share the Drive folder containing takeout zips with the Service Account's email.

Until those are in place, local mode exercises the identical code path.

## How a run works

- **Catalog**: the source (Drive or local dir) lists takeout zips with id, name, modified time, md5.
- **Diff**: `pipeline.processed_files` records every file (ADR-0003); new, changed (md5), or previously-failed files are processed; unchanged files are skipped; source deletions are ignored.
- **Bronze** (ADR-0002): each takeout folder becomes one table (all TEXT + `_takeout`/`_source_file`/`_loaded_at`), monthly files merge, schema drift widens the table.
- **Silver** (ADR-0005): 9 curated families — entities (`sleep_score`, `active_zone_minutes`, `device`, `profile`, `stress`, `spo2`) upsert by natural key; high-frequency families without a defensible key (`hrv`, `temperature`, `activity`) append idempotently (delete-then-insert per source file). Unparseable rows go to `silver.rejected_rows` with a reason, as warnings — not failures; rows deliberately left unmapped (temperature per-sample rows) stay bronze-only.
- **Gold**: read-only views (`gold.daily_health`, `gold.sleep_summary`, `gold.activity_trends`, `gold.freshness`) are the read contract; Grafana queries them as the `dashboard` role (SELECT-only on silver+gold).
- **Run summary**: processed/skipped/failed counts, per-family bronze rows, rejection count. Exit 0 on success; non-zero only on real errors.

## Repository layout

- `src/pipeline/` — `cli.py` (entrypoint), `sources.py` (local adapter + catalog contract), `drive.py` (Drive adapter), `state.py` (ledger + diff), `ingest.py` (bronze), `silver.py` (silver build), `db.py` (DDL + connection + gold views + dashboard role), `runner.py` (per-takeout orchestration), `config.py` (env)
- `Dockerfile` + `requirements.txt` — containerized sync runner (Linux deployment; see below)
- `deploy/` — systemd units (`health-sync.*`, `health-backup.*`) and `backup.sh` (nightly pg_dump)
- `tests/` — E2E suite at the CLI seam (tickets 01/02/04/05/06, 0a Tier 1, 0b gold + role), isolated to a throwaway database
- `grafana/` — provisioned datasource, dashboard (`health.json`), stale-sync alert
- `docs/SPEC.md`, `docs/dashboard.md` (Grafana runbook), `docs/deploy.md` (Linux deployment), `docs/adr/0001–0005`, `CONTEXT.md` (glossary), `ROADMAP.md` (home-lab roadmap: autonomous ingestion, K3S, chatbot)
- `.scratch/takeout-pipeline/issues/` — implementation tickets (gitignored)