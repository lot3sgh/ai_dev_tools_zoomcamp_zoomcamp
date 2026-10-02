# Deploy to a Linux host (Phase 1 — autonomous ingestion)

Everything in this directory targets the **Linux deployment host** (the roadmap's home-lab
server), never the development Mac. No scheduler is installed locally; the repo only carries
the portable pieces:

| Artifact | Purpose |
|---|---|
| `Dockerfile` + `requirements.txt` | the sync CLI as a slim container (`python -m pipeline`, deps pinned+hashed via `uv export`) |
| `docker-compose.yml` → `sync` service | the containerized runner inside the existing compose stack (`profiles: ["sync"]` keeps it out of `up -d`) |
| `deploy/health-sync.{service,timer}` | daily `pipeline sync --source drive` via systemd |
| `deploy/health-backup.{service,timer}` | nightly `pg_dump` → gzip → off-box |
| `deploy/backup.sh` | the backup job itself (retention + optional rclone/rsync off-box push) |

Assumed layout: `/opt/health-pipeline` (override via `REPO_DIR=` in `/etc/health-pipeline.env`).

## Prerequisites on the host

- Debian/Ubuntu-ish Linux with Docker Engine + the compose plugin.
- The repo checked out at `/opt/health-pipeline` (any path, if `REPO_DIR` matches).
- `data/` present (even empty), `.env` populated (same shape as `.env.example`),
  the Service Account key at the path `GOOGLE_APPLICATION_CREDENTIALS` points to.
- Drive preconditions (once): Drive API enabled on the SA's project, folder shared with the SA email.

```bash
# env must exist before compose even interpolates the sync service
sudo install -m 0600 -o root -g root /path/to/.env /opt/health-pipeline/.env
sudo install -m 0600 -o root -g root /path/to/sa-key.json /opt/health-pipeline/

echo 'REPO_DIR=/opt/health-pipeline' | sudo tee /etc/health-pipeline.env
# optional off-box backup targets (exactly one), same file:
#   BACKUP_RCLONE_REMOTE=backup:health-pipeline/     # rclone configured separately
#   BACKUP_RSYNC_TARGET=nas:/srv/backups/health/     # rsync target (or user@host:path)
#   BACKUP_KEEP=14                                   # retention, default 14
```

## Build & verify

```bash
cd /opt/health-pipeline
docker compose build sync                     # or: make image
docker compose --profile sync run --rm sync --help

# smoke: containerized *local* sync against the running stack (no Drive needed)
docker compose --profile sync run --rm -v "$PWD/data:/app/data:ro" sync \
  sync --source local --path /app/data        # 0 files -> exercises env, DB, role provisioning

# full Drive sync, once, by hand
docker compose --profile sync run --rm sync   # = make sync-drive-container
```

The sync container follows the same engine as the bare CLI: it diffs against
`pipeline.processed_files`, so the first scheduled run also heals an empty ledger and
`gold.freshness` + the stale-sync alert start working.

## Install the timers (Linux only — the Makefile refuses on macOS)

```bash
cd /opt/health-pipeline
make install-timers          # copies the 4 units, enable --now both timers
systemctl status health-sync.timer health-backup.timer
journalctl -u health-sync -e        # after the first run
journalctl -u health-backup -e
```

Schedule (both with `Persistent=true` — missed runs fire on next boot):

- `health-sync.timer` — daily 06:15 ±15 min
- `health-backup.timer` — daily 03:10 ±10 min

Remove: `make uninstall-timers`.

## Backup & restore drill

```bash
# one backup now (writes backups/…, prunes to BACKUP_KEEP, pushes off-box if configured)
./deploy/backup.sh            # or: make backup

# restore (plain dumps): decompress into an empty database
gunzip -c backups/health-20261001T031000Z.sql.gz \
  | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# restore (custom dumps): use pg_restore
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < backups/health-20261001T031000Z.dump
```

**Run a full restore drill before trusting the pipeline**: restore into a scratch DB name
(e.g. `health_restore_test`) and compare `SELECT count(*)` per bronze family against the
live DB. Personal health data is non-negotiable — the drill is part of Phase 2's runbook.

## Security notes

- The SA key and `.env` live only on the host, never in the image (mounts, `:ro`).
- The dump contains personal health data. If the off-box target is not fully trusted,
  encrypt before shipping (e.g. `age -e` / `gpg -e` in `backup.sh`’s off-box step).
- Set `BACKUP_FORMAT=custom` if you prefer `pg_dump -Fc` over the plain SQL dump.

## When K3S becomes fair game (roadmap discipline)

Phase 1 deliberately uses systemd timers over the existing compose stack ("pick the simpler
one first"). A cluster only becomes appropriate at Phase 2, and only after a real backup
story exists — at that point the same image becomes a `CronJob` (stateless workload;
Postgres stays host-docker until a PVC + backup story lands).
---

# Health Assistant on the stack (Phase 3)

The assistant (`chat` service) joins the same compose stack, LAN-only. Two-repository
shape (ADR-0006): the generic core `health-assistant-core` lives beside this repo and is
baked into the chat image at build time.

| Artifact | Purpose |
|---|---|
| `Dockerfile.chat` | the chat service image: deps + this repo's `src` + the core's `src` (`PYTHONPATH=/app/src:/app/core_src`), uvicorn factory entry |
| `docker-compose.yml` → `chat` | LAN-bound FastAPI/SSE service, DB as the read-only `chatbot` role, `.env` mounted `:ro` |
| `deploy/eval-corpus.sh` | the eval-corpus merge gate (skips cleanly without `LLM_API_KEY`) |
| `docs/adr/0006-…` | two-repo architecture + execute/privacy contract |

## Prerequisites on the host

The two repos must be siblings, because the compose build uses
`additional_contexts: core: ../health-assistant-core`:

```bash
ls -d /opt/health-pipeline /opt/health-assistant-core   # both checked out
```

`.env` gains the assistant block (see `.env.example` — CHATBOT_DB_PASSWORD for the
read-only role, plus the LLM provider seam with its privacy statement). The `chatbot`
role is provisioned idempotently by the pipeline's `ensure_schemas`, like the
`dashboard` role — the first sync after the change creates it.

## Build & bring up

```bash
cd /opt/health-pipeline
docker compose build chat            # or: make chat
docker compose up -d                 # whole stack incl. chat   (or: make chat-up)
docker compose logs -f chat
```

The service listens on the host's port **8000 only** — no port-forward on the router and
no auth in v1: your home network is the access boundary. A restart preserves the Chat
Log, the role grants, and the configuration; conversations are in-memory and ephemeral
by design.

Development-mode override (live code, no rebuild) — `docker-compose.dev.yml`:

```yaml
services:
  chat:
    volumes:
      - ./src:/app/src:ro
      - ../health-assistant-core/src:/app/core_src:ro
```

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d chat
```

## Provider configuration (and the privacy boundary)

The provider seam is environment-only: `LLM_PROVIDER=stub` runs the deterministic test
provider (dev only); anything else selects the OpenAI-compatible adapter configured by
`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` — Ollama, Bosch BMF, DeepSeek native, or
OpenCode Go are one env block apart.

**Privacy statement, in plain words:** with a hosted gateway (the default), your questions
AND the result rows they produce are sent to the provider. This boundary is explicit and
auditable — every exchange is in `pipeline.chat_log` with the provider that answered —
and there is **no silent local fallback** when the provider is down or the key is missing:
you get a clean refusal instead of a wrong or unlogged answer. Ollama local is the privacy
default whenever you prefer; each provider change must pass the eval gate (below) first.

Verify the exact model id from the OpenCode Console model list before first use and write
it into `LLM_MODEL`; a wrong id fails loudly as a refusal, never as a wrong answer.

## Verify

```bash
# the page is up on the LAN
curl -s http://<host>:8000/ | head -5
# a real question through the provider, streamed
curl -sN -X POST http://<host>:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"how was my sleep last night?","session_id":"verify"}'
# every exchange is auditable in the Chat Log
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT session_id, provider, outcome, row_count, sql FROM pipeline.chat_log ORDER BY id DESC LIMIT 5"
```

## The eval gate (Phase 3 "done when")

```bash
cd /opt/health-pipeline
bash deploy/eval-corpus.sh            # or: make eval-gate
```

The golden corpus (25 pairs, hand-derived from the synthetic takeout) runs against a
fresh scratch database as the `chatbot` role through the real provider selected by the
environment: ≥90% executed-correct on data/empty pairs and 100% on refusals is the merge
gate; below that the script exits non-zero and names the question classes to fix. Without
`LLM_API_KEY` it prints a skip and exits 0 — the deterministic suite never depends on it.
Deterministic mechanics check (no network, no key): `uv run pipeline eval --self-check`.

## Backup

The existing nightdump is a full `pg_dump` of the stack database, so it already includes
`pipeline.chat_log` — the assistant's audit trail (and eval-corpus seed) is covered by
the Phase-1 backup job with no changes. Data is data.
