# How AI tools were used — and reviewed

This project was built with continuous AI assistance; this file is the honest record
(the rubric's "document the AI workflow"). The workflow was: spec → tickets → TDD at
pre-agreed seams → regular typecheck/tests → two-axis review → gate.

## The workflow

1. **Spec & tickets first.** The product spec and tickets were written before code
   (`.scratch/phase-3-health-assistant/issues/`): 7 locked design decisions, an
   execute/privacy contract (ADR-0006), and five tickets in dependency order, each
   with an acceptance checklist.
2. **TDD vertical slices at pre-agreed seams.** Each ticket opened with tests that
   were red before the feature existed, at the seams the spec fixed:
   - chat engine: HTTP/SSE + resulting database state (stub provider, throwaway DB);
   - provider adapters: a local stub gateway speaking the OpenAI protocol;
   - corpus: executed result rows against the fixture DB (values hand-derived,
     drift-locked to the fixture);
   - MCP: JSON-RPC frames, plus a subprocess smoke test.
   No tests assert on orchestrator internals; the suite needs no network and no key.
3. **The AI loop.** Each vertical slice: write the failing test → implement the
   minimum → run that file → mypy → full suite. Prompt context was the repo's own
   documents (`CONTEXT.md` glossary, ADR-0006, ticket checklists), the same context
   anyone re-reading the repo gets.
4. **Regular verification.** `uv run python -m mypy`, single test files after every
   slice, full suite at the end of each ticket, the corpus self-check, and a live
   HTTP smoke test (real uvicorn + curl) for anything user-facing.

## Where the AI was an assistant, not the author

- **Every claim in `security/audit.md` is a real tool run** (gitleaks, pip-audit,
  bandit) with outputs recorded; findings that were fixable were fixed, others were
  triaged with reasons.
- **The eval corpus is the referee for AI behavior**: text-to-SQL is only adopted
  because a golden corpus (25 hand-derived pairs) gates merges at ≥90% executed
  accuracy and 100% refusals. The corpus run against the real provider found a
  gateway integration bug (missing `x-opencode-session`, 0% run) which the AI
  diagnosed via probe and fixed — then re-gated to 21/21.
- **Human/agent review of AI output** happened on two axes (spec fidelity vs repo
  standards) before merge; residuals were operator-environment items, not code gaps.

## Prompt/context fidelity

The same documents an agent is pointed at are the ones the product uses: DB `COMMENT
ON` text feeds the LLM's tool contract (grain semantics live in the schema, versioned),
the corpus doubles as the few-shot bank, and `AGENTS.md`/`CONTEXT.md` set the
repository language for any agent entering the project. There is no "secret prompt":
what the product's model sees is generated from the database and the corpus.

## Verification checklist (reproduce any claim)

```bash
uv run pytest -q                       # backend + agent-hooks + MCP (79+ tests)
cd frontend && npm test                # frontend protocol tests
uv run python -m mypy                  # typecheck
uv run pipeline eval --self-check      # deterministic corpus self-check
bash deploy/eval-corpus.sh             # real-provider gate (skips without key)
docker run --rm -v "$PWD:/repo" -w /repo zricethezav/gitleaks:latest git --no-banner
```