# Google Health Takeout → Postgres Pipeline

Ingests Google Health (Fitbit) takeout archives from Google Drive (or a local folder of zips) into Postgres with a **bronze/silver** layering. Every CSV lands verbatim in bronze (TEXT, family-per-table, with provenance); the prime domains — sleep, active zone minutes, devices, profile — are also typed and keyed in silver. See `docs/SPEC.md` for the full spec and `docs/adr/` for the architecture decisions.

## Quickstart

```bash
# 1. Bring up Postgres (docker compose; host port 5433, DB creds from .env)
docker compose up -d db   # or: make up

# 2. Install deps (uv, Python >= 3.12)
uv sync --extra dev

# 3. Sync the local sample archive (data/*.zip) into bronze + silver
make sync-local

# 4. Query it
docker exec -it health-pipeline-postgres psql -U pipeline -d health_pipeline
```

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | start / stop the Postgres container |
| `make sync-local` | sync takeout zips from `./data` |
| `make sync-drive` | sync takeout zips from Google Drive |
| `make test` | E2E suite (synthetic fixture, throwaway Postgres) |
| `make typecheck` | mypy over the package |

Direct CLI: `uv run pipeline sync --source local --path <dir>` / `--source drive`, with `--limit N` to scope a dev run.

## Environment

Credentials live in `.env` (gitignored; `.env.example` shows the shape):

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `PGHOST`, `PGPORT` — database
- `GOOGLE_APPLICATION_CREDENTIALS` — Service Account key for Drive mode (optional for local mode; defaults to the key at repo root)

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
- **Silver**: typed, keyed tables (`sleep_score`, `active_zone_minutes`, `device`, `profile`) upserted by natural key; unparseable rows go to `silver.rejected_rows` with a reason, as warnings — not failures.
- **Run summary**: processed/skipped/failed counts, per-family bronze rows, rejection count. Exit 0 on success; non-zero only on real errors.

## Repository layout

- `src/pipeline/` — `cli.py` (entrypoint), `sources.py` (local adapter + catalog contract), `drive.py` (Drive adapter), `state.py` (ledger + diff), `ingest.py` (bronze), `silver.py` (silver build), `db.py` (DDL + connection), `runner.py` (per-takeout orchestration), `config.py` (env)
- `tests/` — E2E suite at the CLI seam (tickets 01/02/04/05/06)
- `docs/SPEC.md`, `docs/adr/0001–0003`, `CONTEXT.md` (glossary)
- `.scratch/takeout-pipeline/issues/` — implementation tickets (gitignored)