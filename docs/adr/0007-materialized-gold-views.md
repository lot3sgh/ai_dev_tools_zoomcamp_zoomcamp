# Gold's heavy views are materialized; refreshed after each sync

Status: accepted (2026-10-06). Supersedes decision #1 of ADR-0005
("The gold layer is read-only SQL views over silver — no mart tooling").

The three aggregate gold objects — `daily_health`, `sleep_summary`,
`activity_trends` — are **materialized views**; `freshness` stays a live SQL
view (it is operational and must reflect the latest sync ledger). The
definitions still live in the pipeline's idempotent DDL (`db.py`), and a sync
rebuilds them via `pipeline.db.refresh_gold()` at the end of `run_sync`.

Why the change: with real takeouts `silver.activity` holds ~9.9M rows. The gold
views aggregated it on **every** query, and the host's storage delivers
~65 MB/s, so a full-history `gold.daily_health` read cost ~35 s — well past the
assistant's 10 s statement timeout (the execute contract), so those questions
refused rather than answered. Materializing moves that cost to the
once-per-sync refresh and makes reads O(rows in the view) = O(hundreds).

Why it is still "no mart tooling": the definitions live in the pipeline DDL,
there is no dbt or second scheduler, and the refresh is a single function the
sync calls. The read contract is unchanged — same relation names, same columns,
same `COMMENT ON` grain documentation, same `dashboard`/`chatbot` grants — so
Grafana and the assistant are unaffected (the tool's `pg_class`+`pg_description`
doc lookup and the lexical surface guard both key on relation name, not kind).

The refresh is non-concurrent (a short ACCESS EXCLUSIVE lock during the sync);
unique indexes exist on each materialized view so a `REFRESH ... CONCURRENTLY`
is a one-line change if that lock ever matters.
