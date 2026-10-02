# Agent hooks (guardrails)

Guardrails are enforced code, not prose. Today there is one, with tests:

## SQL-surface guard — `agent_hooks/sql_surface_guard.py`

**Position:** first step of `RunHealthQuery.run()` — before the EXPLAIN dry-run ever
sees the statement.

**What it refuses:**
- multi-statement input (`SELECT 1; DROP …`), any trailing-semicolon smuggling;
- statements that do not lead with a read (`SELECT`/`WITH`/`EXPLAIN`/`SHOW`) —
  `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/GRANT/…` are refused outright;
- any relation outside the Semantic Layer by name: `bronze.*` always,
  `silver.*` except the whitelist (`sleep_score`, `device`, `profile`), `gold.*`
  except the four views.

**Why it exists:** the primary enforcement is the `chatbot` role's grants (bronze is
unreachable by construction — ADR-0006), but a guard at the tool layer makes the
contract survive a misconfigured role, a leaked grant, or a hostile prompt: the
refusal happens before anything reaches the executor.

**Tests:** `tests/test_sql_surface_guard.py` — unit contract + a tool-level test
proving a write is refused without touching the database.

Same-shape rules for the LLM itself live in the system prompt and the eval corpus;
this is the deterministic layer underneath them.

Adding a hook: implement it as a pure function, test the semantics, then wire it
into the tool (or the service) with a comment naming this directory as the home of
guardrails. Correlate with `docs/permissions.md` — the doc mirrors enforcement.