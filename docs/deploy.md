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