# Dashboard (Phase 0b) — runbook & smoke checklist

The gold views (`gold.daily_health`, `gold.sleep_summary`, `gold.activity_trends`,
`gold.freshness`) are the read contract; Grafana is the only consumer that needs wiring.
It connects as the read-only `dashboard` role (provisioned by every sync run when
`DASHBOARD_DB_PASSWORD` is set).

## One-time setup

1. Pick passwords and put them in `.env` (gitignored):
   - `DASHBOARD_DB_PASSWORD` — the read-only analytics login (Grafana uses it)
   - `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` — Grafana's own login (defaults:
     `admin` / `change_me_grafana`)
2. Start the stack: `docker compose up -d` (Postgres must be healthy first).
3. Open http://localhost:3000, log in with the Grafana admin credentials.

## Smoke checklist (manual, run after any change to provisioning)

- [ ] Grafana starts with no provisioning errors; datasource "Health Pipeline Postgres"
      appears in Configuration → Data sources.
- [ ] The datasource connects: "Save & test" succeeds (proves the `dashboard` role,
      `DASHBOARD_DB_PASSWORD`, and network wiring).
- [ ] The "Health Overview" dashboard is present and every panel returns rows
      (sleep score, weekly steps, HRV, pipeline freshness).
- [ ] Panels query gold views only — no silver/bronze queries (the datasource cannot
      read bronze anyway: the role is SELECT-only on silver+gold).
- [ ] **Alert smoke**: log into Postgres as the pipeline user and either add a fake
      ledger entry with an old `processed_at` or pause syncing for 5+ days, then
      confirm "Takeout sync is stale" enters Firing state (Alerting → alerts).
- [ ] Freshness panel shows the last synced Takeout and its rejection count.

## Notes

- Nothing in this phase writes to the database: the pipeline, the ledger, and the
  bronze/silver layers are untouched by Grafana.
- To grant the dashboard role access to future silver/gold tables, `GRANT SELECT` must
  be run per table (or default privileges set by the pipeline superuser) — the
  provisioning in the pipeline offers the schema-level grant only for tables that
  exist at provisioning time.
- **Known caveat (observed 2026-10-01):** the stale-sync alert reads `MAX(processed_at)` from
  `gold.freshness`, so it keys off the ledger. If `pipeline.processed_files` is empty (e.g.
  the `pipeline` schema was dropped/recreated without a fresh sync), `gold.freshness` returns
  no rows and the alert sits in NoData instead of firing — heal by re-running a sync.
- The provisioned alert covers staleness (5 days) only; a rejections-spike alert is a
  pending item (ROADMAP Phase 0b, Panels box).