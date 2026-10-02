#!/usr/bin/env bash
# Nightly Postgres backup for the Health Takeout pipeline (deployment target: Linux host).
#
#   backups/health-<UTC-stamp>.sql.gz   <- compressed plain dump (or -Fc custom if BACKUP_FORMAT=custom)
#   retention:           keeps the BACKUP_KEEP newest archives (default 14)
#   off-box:             optional, exactly one of BACKUP_RCLONE_REMOTE / BACKUP_RSYNC_TARGET
#
# Configure via /etc/health-pipeline.env (see docs/deploy.md).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

KEEP="${BACKUP_KEEP:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EXT="${BACKUP_FORMAT:-plain}"   # plain -> .sql.gz ; custom -> .dump
if [[ "${BACKUP_FORMAT:-plain}" == "custom" ]]; then
  OUT="backups/health-${STAMP}.dump"
  COMPRESSOR="cat"
else
  OUT="backups/health-${STAMP}.sql.gz"
  COMPRESSOR="gzip"
fi
mkdir -p backups

echo "backup: ${EXT} dump -> ${OUT}"
if [[ "${BACKUP_FORMAT:-plain}" == "custom" ]]; then
  docker compose exec -T db pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "${OUT}"
else
  docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "${OUT}"
fi

# Retention: keep the KEEP newest archives.
ls -1t backups/health-* 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  echo "backup: pruning ${old}"
  rm -f "${old}"
done

# Off-box shipping (optional; configure exactly one in /etc/health-pipeline.env).
if [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]]; then
  command -v rclone >/dev/null || { echo "backup: BACKUP_RCLONE_REMOTE set but rclone is not installed" >&2; exit 1; }
  rclone copy "${OUT}" "${BACKUP_RCLONE_REMOTE}"
  echo "backup: shipped off-box via rclone -> ${BACKUP_RCLONE_REMOTE}"
elif [[ -n "${BACKUP_RSYNC_TARGET:-}" ]]; then
  rsync -az "${OUT}" "${BACKUP_RSYNC_TARGET}"
  echo "backup: shipped off-box via rsync -> ${BACKUP_RSYNC_TARGET}"
fi

echo "backup: ok (${OUT})"