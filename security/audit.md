# Security & audit report

Deterministic scan artifacts, run from the repository root on 2026-10-02, master
`a6c1584…` (monorepo). Every finding below is reproducible with the command shown.

## 1. Secret scanning — gitleaks

```bash
docker run --rm -v "$PWD:/repo" -w /repo zricethezav/gitleaks:latest git --no-banner --redact
```

**Result: no leaks found** across all 32 commits (including the `health-assistant-core`
subtree history). `.env`, both Google SA keys, and `data/` are gitignored by design;
the SA key is additionally excluded from the Docker build context (`.dockerignore`).

## 2. Dependency vulnerabilities — pip-audit

```bash
/tmp/auditvenv/bin/pip-audit -r requirements.txt     # pinned, hashed requirements
```

**Result: no known vulnerabilities found** (current advisories at scan time).

## 3. Static analysis — bandit

```bash
uv run --with bandit bandit -r backend/src agent-hooks mcp-server
```

**5 findings, 0 actionable Medium+:**

| ID | Severity | Location | Status |
|---|---|---|---|
| B101 `assert_used` ×4 | Low | `engine.py` (invariant: tool registered; stream ended with outcome), `eval.py` (corpus tool present) | **Accepted** — programmer invariants guarded by immediate context, not input validation; code never runs under `-O`. |
| B324 `hashlib.md5` | High signal / low reality | `pipeline/sources.py` — takeout identity checksum for sync dedup (`_source_file` provenance, logged in `pipeline.processed_files.md5`) | **Accepted, documented** — MD5 is an *identity* hash for dedup, not a security primitive; it is checked against nothing adversarial. Migrating to SHA-256 would break idempotency for already-processed archives (their stored md5 would no longer match) — revisit only with a one-time re-map migration. |

**Fixed during this audit** (were Medium, B608 string-built SQL): three `cur.execute`
sites in `pipeline/{cli,ingest,silver}.py` built table names via string interpolation
(names come from `information_schema`, not user input). All three now use
`psycopg.sql.Identifier` (commit lands with this report). The assistant's *intentional*
text-to-SQL surface (`assistant/tool.py`) passes model SQL to `EXPLAIN` under the
execute contract — that is the product, bounded by role grants, the SQL-surface guard,
and the dry-run rails, not a vector.

## 4. PR audit (human-AI review record)

Before merge, the phase-3 work was reviewed on two axes (spec fidelity vs repo
standards):

- **Spec axis**: tickets 02–05 checked against `.scratch/phase-3-health-assistant/issues/`
  — 2 residuals, both operator-environment dependent (model-id verification against
  the OpenCode Console, first live keyed run); the corpus gate then certified the real
  provider at 21/21 data + 4/4 refusals.
- **Standards axis**: 4 findings — all fixed before merge (speculative generality
  removed; SSE outcome now carries the truncation flag; env bootstrap centralized;
  unused imports dropped).
- **Independent tool findings**: the OpenCode Go gateway rejected requests without
  `x-opencode-session`; surfaced by the eval gate as a 0% run, root-caused with a
  live probe, fixed by threading `session_id` through the provider seam, then
  re-certified. This is the audit loop working: a provider integration was refused
  loudly, diagnosed, and gated again before adoption.

## 5. Agent/extension security notes

- `docs/permissions.md` defines every actor's rights; the runtime enforces them
  (role grants, guard, rails, Chat Log).
- The MCP server runs only the registered tools on the caller's own engine
  (no new rights); refusals are non-error outputs; unknown methods/tools error
  cleanly (JSON-RPC −32601/−32602).
- The custom-agent profile forbids touching secrets, writing DB rows, and enabling
  silent provider fallback — see `custom-agent/README.md`.

## 6. Data policy pointer

See `security/policy.md` for the AI tool/data policy: what health data transits the
provider, what is logged, retention, and the local-provider option.