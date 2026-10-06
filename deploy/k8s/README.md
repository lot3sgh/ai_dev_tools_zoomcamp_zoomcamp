# Health Assistant on local single-node k3s

The same system as `docker-compose.yml`, expressed as Kubernetes objects and deployed
to the bundled k3s cluster (Traefik ingress + local-path volumes). LAN-only, like the
compose stack: the node's Traefik service is bound to the host, never port-forwarded.

## Host prerequisite: firewalld

On a host running firewalld, trust the CNI interfaces before deploying, or Traefik
balances to pod IPs that firewalld's FORWARD filter rejects — every page returns **502**
while ClusterIP (and thus the bootstrap Job) still succeeds:

```bash
sudo ./deploy/k8s/host-prereqs.sh      # trusts cni0 + flannel.1, restarts k3s once
```

Caveat: a later `firewall-cmd --reload` flushes k3s's node iptables rules (ClusterIP and
NodePort break until `sudo systemctl restart k3s`). The trusted-zone bindings are
persistent, so a reboot is safe; a manual reload is not.

## One-command deploy

```bash
./deploy/k8s/deploy.sh
```

It reads `.env`, generates (and persists to the gitignored `.k8s-secrets.env`) the
read-only `chatbot`/`dashboard` role passwords and the Grafana admin password, builds
`health-pipeline-{sync,chat}:latest`, imports them into k3s's containerd, creates the
ConfigMap/Secrets/Grafana ConfigMaps, applies the manifests, waits for Postgres, and
runs the schema + role bootstrap Job.

Then:

```
http://<node-ip>/          -> the chat UI
http://<node-ip>/grafana   -> Grafana (user `admin`)
```

## Objects

| File | Objects | Replaces |
|---|---|---|
| `00-namespace.yaml` | `health-pipeline` namespace | — |
| `01-config.yaml` | ConfigMap `health-pipeline-config` | `.env` (non-secret half) |
| `10-postgres.yaml` | StatefulSet/Service `db` + 5Gi local-path PVC | compose `db` |
| `20-init-job.yaml` | Job `health-pipeline-init` (schemas + read-only roles) | first `ensure_schemas` |
| `30-chat.yaml` | Deployment/Service `chat` | compose `chat` |
| `40-grafana.yaml` | Deployment/Service `grafana` + 1Gi PVC | compose `grafana` |
| `50-ingress.yaml` | Traefik Ingress (path-based: `/`, `/grafana`) | compose port mappings |
| `60-sync-cronjob.yaml` | CronJob `health-pipeline-sync` (06:15 UTC) | `health-sync.timer` |
| `70-backup-cronjob.yaml` | CronJob `health-pipeline-backup` (03:10 UTC) + 10Gi PVC | `health-backup.timer` |

Secrets are **not** committed: `deploy.sh` creates
`health-pipeline-secrets` (DB password, `CHATBOT_DB_PASSWORD`, `DASHBOARD_DB_PASSWORD`,
`GRAFANA_ADMIN_PASSWORD`, optional `LLM_API_KEY`) and `health-pipeline-sa-key`
(the Drive Service Account JSON).

## Provider configuration

The LLM seam is environment-only, exactly as `docs/deploy.md` describes. With nothing
set the page serves but `/api/chat` returns **503 offline** (no silent fallback). To
enable chat, put the provider block in `.env` and rerun `deploy.sh`:

```bash
# Ollama on the node (privacy default)
LLM_PROVIDER=ollama
LLM_BASE_URL=http://10.0.0.138:11434/v1
LLM_MODEL=llama3.1
```

`LLM_BASE_URL` must be reachable from inside the cluster. For a host-local Ollama,
`http://<node-ip>:11434/v1` works when the pod network routes to the host.

## Operations

```bash
kubectl -n health-pipeline get pods,svc,ingress,cronjob
kubectl -n health-pipeline logs deploy/health-pipeline-chat -f
kubectl -n health-pipeline create job --from=cronjob/health-pipeline-sync sync-now
kubectl -n health-pipeline create job --from=cronjob/health-pipeline-backup backup-now
```

Verify (same probes as `ops/diagnosis.md`):

```bash
NODE_IP=$(kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
curl -s -o /dev/null -w '%{http_code}\n' "http://$NODE_IP/"          # 200: the page
curl -sN -X POST "http://$NODE_IP/api/chat" -H 'Content-Type: application/json' \
  -d '{"question":"how was my sleep last night?","session_id":"verify"}'
kubectl -n health-pipeline exec statefulset/health-pipeline-db -- \
  psql -U pipeline -d health_pipeline -c \
  "SELECT outcome, count(*) FROM pipeline.chat_log GROUP BY 1"
```

### Restore drill

The backup CronJob writes `health-<UTC>.sql.gz` into the `health-pipeline-backups` PVC.
Copy one out and restore into a scratch database:

```bash
POD=$(kubectl -n health-pipeline get pod -l app.kubernetes.io/name=health-pipeline-db -o name)
kubectl -n health-pipeline exec "$POD" -- ls -1t /backups | head
```

To restore into a scratch DB, decompress the dump and pipe it to `psql` against a new
database on `db`; compare per-family row counts to the live DB, as `docs/deploy.md`
requires for personal health data.

## Teardown

```bash
kubectl delete namespace health-pipeline      # deletes PVCs too — dumps are gone
```

To keep data, back up the PVC first. The generated `.k8s-secrets.env` stays on the
host, so a redeploy reuses the same role passwords.
