# Gold is a SQL view layer; bronze→silver promotion follows a rule

Status: accepted (landed in ROADMAP Phases 0a/0b, 2026-10-01).

Two related decisions from the home-lab roadmap's guardrails, now written down where the code can cite them.

## 1. The gold layer is read-only SQL views over silver — no mart tooling

`gold` is four views (`daily_health`, `sleep_summary`, `activity_trends`, `freshness`) defined in the pipeline's
idempotent DDL (`db.py`) and consumed by Grafana through the read-only `dashboard` role. The views ARE the mart;
there is no dbt, no materialization, no separate analytics schema.

Why: at this scale (single user, ~11M bronze rows) Postgres aggregates trivially and views stay definitionally
in sync with silver — a materialized layer would add freshness/refresh machinery with no query the views cannot
already answer. This extends ADR-0004's reasoning (no dlt, no framework layer you don't need). Revisit triggers:
a consumer that needs sub-second latency over the full history, or a second source/destination that makes the
view definitions a governance problem.

The read contract is enforced by permissions, not by convention: the `dashboard` role is SELECT-only on
`silver` and `gold`, and never sees `bronze`.

## 2. Bronze→silver promotion rule: typed/keyed value + defensible key + clean time grain

A bronze family is promoted to a silver table when all three hold:

- reports/dashboards ask for its values **typed and keyed** (not just queryable from bronze),
- the family has a **defensible natural key**, and
- it has a **clean time grain**.

Everything else stays bronze-only (queryable via views). The rule also admits a third outcome: a promoted family
whose data has no defensible key runs in **append mode** — typed, but with rerun idempotency from
delete-then-insert per `_source_file` (the bronze-level pattern from ADR-0002), because inventing a key would
silently collapse legitimate duplicates.

Applied today (Phase 0a):

- Keyed upsert: `sleep_score` (`sleep_log_entry_id`), `active_zone_minutes` (`date_time`, `heart_zone_id`),
  `device` (`wire_id`), `profile` (`id`), `stress` (`date`), `spo2` (`timestamp`).
- Append mode: `hrv`, `temperature` (night summaries only; per-sample rows are skipped, not rejected),
  `activity` (verified duplicate `(timestamp, data_source)` pairs in the family forbid inventing a key).
- Bronze-only: the ~13 remaining families (`global_export_data`, `health_fitness_data_googledata`, …) — typed/keyed
  value not (yet) demanded, or no defensible key at intraday grain.

Promote further families by the same rule when reports ask; demote/deprecate by the same reasoning in reverse.