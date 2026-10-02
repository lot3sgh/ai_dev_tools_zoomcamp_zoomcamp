# Capability: run_health_query

The assistant's single tool: plain-language health questions become read-only SQL over
the Semantic Layer, executed under a strict contract, answered in natural language.

## Contract

- **Surface** (permissions + guard): `gold.daily_health`, `gold.sleep_summary`,
  `gold.activity_trends`, `gold.freshness`, `silver.sleep_score`, `silver.device`,
  `silver.profile`. Bronze and `silver.hrv`/`temperature`/`activity`/… are unreachable
  by role construction and refused by the tool-layer guard.
- **Rails**: EXPLAIN dry-run with a 1M-row cardinality guard → refusal; 10 s
  `statement_timeout`; `work_mem` 32 MB; 500-row cap with truncation disclosure;
  SQL failures feed the error text back for ≤2 repairs, then a refusal.
- **Semantics from the database**: `COMMENT ON` text (grain: night = the date sleep
  started; duplicate HRV rows are real; `spo2.value` > `average_value`) is injected
  into the model context at query time via `docs()`.
- **Outcomes**: answered / refused (incl. honest "no data for that range") / error;
  every exchange is logged to `pipeline.chat_log` with provider, SQL, row count,
  thumbs.

## Registration

```python
from assistant_core import Registry
from assistant import register_health_tools

registry = Registry()
register_health_tools(registry)          # adds run_health_query with default rails
tool = registry.get("run_health_query")
```

## Guardrail (agent-hooks)

Before the EXPLAIN dry-run, `agent_hooks.sql_surface_guard.guard_sql()` refuses
multi-statement input, non-read statements, and any relation outside the surface —
defense in depth on top of the role grants (see `agent-hooks/`).

## Measured quality

The eval corpus (25 pairs: per-night, per-day, per-week, detail, operational,
empty-range, refusal) is the merge gate: ≥90% executed-correct, 100% refusals
(`make eval-gate`; deterministic path: `uv run pipeline eval --self-check`).