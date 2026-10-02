# Operational diagnosis

A checkable snapshot of the stack's health on the development host (commands +
outputs, 2026-10-02). The same probes are the post-deploy verification for the Linux
host (`docs/deploy.md`).

## Stack containers and health

```bash
docker ps -a --filter "name=health-pipeline" --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
```

```
health-pipeline-grafana    Up 32 hours   0.0.0.0:3000->3000/tcp
health-pipeline-postgres   Up 38 hours (healthy)   0.0.0.0:5433->5432/tcp
```

Postgres is `healthy` via its healthcheck (`pg_isready`). Grafana serves (302 → login).

## Service boot

```bash
uv run uvicorn assistant.server:create_app --factory --port 8000 --log-level warning
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/
```

`GET / -> 200` — the single-page UI is served.

## Database state

```sql
SELECT outcome, count(*) FROM pipeline.chat_log GROUP BY 1 ORDER BY 2 DESC;
SELECT rolname FROM pg_roles WHERE rolname IN ('chatbot','dashboard','pipeline');
SELECT count(*) FROM gold.daily_health;
```

```
chat_log outcomes: [('answered', 6), ('refused', 1), ('error', 1)]
roles: chatbot, dashboard, pipeline      (all provisioned idempotently)
gold.daily_health rows: 496              (real ingested data)
```

Interpretation: answered/refused/error mix is expected (refusals = out-of-surface
questions, e.g. blood pressure; 1 error predates the provider fix). Roles are present;
the Semantic Layer has data; Grafana and the chat page answer.

## The eval gate as a health check

```bash
uv run pipeline eval --self-check          # deterministic: fixture DB + stub
make eval-gate                             # real provider (skips without LLM_API_KEY)
```

The deterministic path certifies: 25 corpus pairs, 21/21 data, 4/4 refusals,
exit 0. A failing pair names the question class to fix — the gate doubles as the
canary for both schema changes and provider changes.

## Backup coverage

`deploy/backup.sh` takes a full `pg_dump` nightly (systemd timer on the Linux host),
which includes `pipeline.chat_log` — "data is data". Retention 14 days + optional
off-box push; restore drill documented in `docs/deploy.md`.