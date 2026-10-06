#!/usr/bin/env bash
# Deploy the Health Assistant stack to the local single-node k3s cluster.
#
#   ./deploy/k8s/deploy.sh              # build images, create config/secrets, apply, wait
#   SKIP_BUILD=1 ./deploy/k8s/deploy.sh # reuse the docker images already built
#
# What it does, in order:
#   1. reads the repo .env (DB + optional LLM/Grafana settings)
#   2. generates/persists the read-only role + Grafana passwords (.k8s-secrets.env, gitignored)
#   3. builds health-pipeline-{sync,chat}:latest and imports them into k3s's containerd
#   4. creates the namespace, ConfigMap, Secret, SA-key Secret, and Grafana ConfigMaps
#   5. applies the manifests, waits for Postgres, runs the schema/role bootstrap Job
#
# It never prints secret values. Rerunning is idempotent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

K8S_DIR="$REPO_ROOT/deploy/k8s"
NS="health-pipeline"
K3S_CTR="${K3S_CTR:-/usr/local/bin/k3s}"
GEN_FILE="$REPO_ROOT/.k8s-secrets.env"   # gitignored; holds generated passwords

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v kubectl >/dev/null || die "kubectl not found"
docker info >/dev/null 2>&1 || die "docker not available"
sudo -n true 2>/dev/null || die "passwordless sudo is required to import images into k3s containerd"

# --- 1. configuration from .env (environment wins over the file) ---------------
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a

POSTGRES_DB="${POSTGRES_DB:-health_pipeline}"
POSTGRES_USER="${POSTGRES_USER:-pipeline}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
LLM_PROVIDER="${LLM_PROVIDER:-}"
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_MODEL="${LLM_MODEL:-}"
LLM_API_KEY="${LLM_API_KEY:-}"

rand_secret() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }

# get_secret VAR: existing env value > persisted value > freshly generated+persisted.
get_secret() {
  local var="$1" current stored value
  current="$(printenv "$var" 2>/dev/null || true)"
  if [ -n "$current" ]; then printf '%s' "$current"; return; fi
  if [ -f "$GEN_FILE" ]; then
    stored="$(sed -n "s/^${var}=//p" "$GEN_FILE" | head -1)"
    if [ -n "$stored" ]; then printf '%s' "$stored"; return; fi
  fi
  value="$(rand_secret)"
  ( umask 077; printf '%s=%s\n' "$var" "$value" >> "$GEN_FILE" )
  printf '%s' "$value"
}

[ -n "$POSTGRES_PASSWORD" ] || die "POSTGRES_PASSWORD is empty — populate .env first"
CHATBOT_DB_PASSWORD="$(get_secret CHATBOT_DB_PASSWORD)"
DASHBOARD_DB_PASSWORD="$(get_secret DASHBOARD_DB_PASSWORD)"
GRAFANA_ADMIN_PASSWORD="$(get_secret GRAFANA_ADMIN_PASSWORD)"

# --- 2. build + import the two application images ------------------------------
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  log "building images (health-pipeline-sync, health-pipeline-chat)"
  docker compose build sync chat
fi

import_image() {
  local image="$1"
  docker image inspect "$image" >/dev/null 2>&1 || die "image $image not built (run without SKIP_BUILD)"
  log "importing $image into k3s containerd"
  docker save "$image" | sudo "$K3S_CTR" ctr images import -
}
import_image health-pipeline-sync:latest
import_image health-pipeline-chat:latest

# --- 3. namespace + configuration objects --------------------------------------
log "creating namespace/config/secret objects"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap health-pipeline-config -n "$NS" \
  --from-literal=POSTGRES_DB="$POSTGRES_DB" \
  --from-literal=POSTGRES_USER="$POSTGRES_USER" \
  --from-literal=PGHOST=db \
  --from-literal=PGPORT=5432 \
  --from-literal=GRAFANA_ADMIN_USER="$GRAFANA_ADMIN_USER" \
  --from-literal=LLM_PROVIDER="$LLM_PROVIDER" \
  --from-literal=LLM_BASE_URL="$LLM_BASE_URL" \
  --from-literal=LLM_MODEL="$LLM_MODEL" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic health-pipeline-secrets -n "$NS" \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=CHATBOT_DB_PASSWORD="$CHATBOT_DB_PASSWORD" \
  --from-literal=DASHBOARD_DB_PASSWORD="$DASHBOARD_DB_PASSWORD" \
  --from-literal=GRAFANA_ADMIN_PASSWORD="$GRAFANA_ADMIN_PASSWORD" \
  --from-literal=LLM_API_KEY="$LLM_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

# Service Account key for Drive sync (never baked into an image).
SA_KEY="${GOOGLE_APPLICATION_CREDENTIALS:-}"
case "$SA_KEY" in
  "") SA_KEY="$(ls wise-weaver-*.json 2>/dev/null | head -1)" ;;
  /*) ;;
  *) SA_KEY="$REPO_ROOT/$SA_KEY" ;;
esac
if [ -n "$SA_KEY" ] && [ -f "$SA_KEY" ]; then
  kubectl create secret generic health-pipeline-sa-key -n "$NS" \
    --from-file=sa-key.json="$SA_KEY" --dry-run=client -o yaml | kubectl apply -f -
else
  printf 'warning: no Service Account key found — the Drive sync CronJob will fail until health-pipeline-sa-key exists\n' >&2
fi

# Grafana provisioning ConfigMaps, materialized from the repo's grafana/ tree.
kubectl create configmap health-pipeline-grafana-datasources -n "$NS" \
  --from-file=datasources.yml=grafana/provisioning/datasources/datasources.yml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap health-pipeline-grafana-dashboards-provider -n "$NS" \
  --from-file=dashboard.yml=grafana/provisioning/dashboards/dashboard.yml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap health-pipeline-grafana-alerting -n "$NS" \
  --from-file=stale-sync.yaml=grafana/provisioning/alerting/stale-sync.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap health-pipeline-grafana-dashboards -n "$NS" \
  --from-file=health.json=grafana/dashboards/health.json \
  --dry-run=client -o yaml | kubectl apply -f -

# --- 4. workloads --------------------------------------------------------------
log "applying workloads"
# NB: 01-config.yaml is documentation/defaults only — the ConfigMap above is the
# source of truth, so re-applying it would clobber the .env-derived values.
for f in 00-namespace 10-postgres 30-chat 40-grafana 50-ingress \
         60-sync-cronjob 70-backup-cronjob; do
  kubectl apply -f "$K8S_DIR/$f.yaml"
done

log "waiting for Postgres"
kubectl -n "$NS" rollout status statefulset/health-pipeline-db --timeout=300s

# ConfigMap/Secret changes do not restart pods; roll the consumers so env updates land.
log "restarting config consumers"
kubectl -n "$NS" rollout restart deployment/health-pipeline-chat deployment/health-pipeline-grafana

log "running schema/role bootstrap Job"
kubectl -n "$NS" delete job health-pipeline-init --ignore-not-found
kubectl apply -f "$K8S_DIR/20-init-job.yaml"
kubectl -n "$NS" wait --for=condition=complete job/health-pipeline-init --timeout=600s

log "waiting for the services"
kubectl -n "$NS" rollout status deploy/health-pipeline-chat --timeout=180s
kubectl -n "$NS" rollout status deploy/health-pipeline-grafana --timeout=180s

# --- 5. summary ----------------------------------------------------------------
NODE_IP="$(kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"
log "deployed"
kubectl -n "$NS" get pods -o wide
printf '\nChat UI:  http://%s/\nGrafana:  http://%s/grafana   (user: %s)\n' \
  "$NODE_IP" "$NODE_IP" "$GRAFANA_ADMIN_USER"
if [ -z "$LLM_API_KEY" ] && [ "$LLM_PROVIDER" != "stub" ]; then
  printf 'Note: no LLM provider is configured — the page serves, /api/chat returns 503 (offline).\n'
  printf '      Set LLM_PROVIDER/LLM_BASE_URL/LLM_MODEL/LLM_API_KEY in .env and rerun to enable chat.\n'
fi
