# Phase 3: two-repository shape for the assistant, and its execute/privacy contract

Status: accepted (Phase 3 design session, 2026-10-02; accompanies the Phase 3 spec and tickets).

The Health Assistant is a conversational text-to-SQL layer over the Semantic Layer. This ADR records
the two decisions the rest of Phase 3 builds on: where the generic code lives, and the contract that
makes direct text-to-SQL safe on private health data.

## 1. Two repositories: the generic core is its own repo from day one

- `health-assistant-core` — the provider-agnostic chatbot core: orchestrator loop (with streaming
  token/sql/outcome events), the OpenAI-compatible provider adapter seam, the capability-tools
  registry. Zero runtime dependencies; deterministic tests (scripted stub + a local stub gateway),
  no network, no key.
- `health-pipeline` — the health side only: the `run_health_query` tool registration over the
  Semantic Layer, the eval corpus + merge gate, the `chatbot` role provisioning, the Chat Log, the
  FastAPI/SSE service + single-file UI, and the compose glue.

> **Addendum (2026-10-02, operator decision): monorepo.** The two repositories were consolidated
> into one: `health-assistant-core` now lives as `health-assistant-core/` inside this checkout
> (merged as a subtree, its full history preserved). The architectural seam is unchanged — the
> core remains a self-contained package with its own pyproject/tests, and the compose build still
> wires it as an additional build context (`core: ./health-assistant-core`); the path sources
> were updated accordingly. Rationale: single push/branch for the whole assistant, simpler
> deployment on the Linux host (one checkout), while the core stays independently testable.

Why now: Phase 4's "pod-able core" is a continuation, not a rewrite — other home-lab projects
(media index, energy logs, …) register their own capability tools without touching the core's code.
The composition seam is a `Registry` of `Tool`s; this repo registers exactly one
(`run_health_query`). The build mechanics: development mounts both repositories, production bakes
the core into the chat image (compose `additional_contexts`), so the deployed image is one artifact.

Revisit triggers: a second registered tool in this repo (then the registration seam becomes a
discovery convention), or a second project consuming the core (then the core publishes a reusable
container/chart — Phase 4's stated exit criterion).

## 2. The execute contract: bounded, role-enforced, measured

Text-to-SQL is only adopted because every question it answers is bounded by construction:

- **Surface by role, not by prompt.** The `chatbot` role is SELECT-only on exactly the Semantic Layer
  (four gold views + `silver.sleep_score`/`device`/`profile`); bronze and the high-volume silver
  families are unreachable by grants, so a hallucinated `bronze.…` reference is a permission error,
  never a data access.
- **Rails per execution.** `EXPLAIN` dry-run with a cardinality guard (est. 1M rows → refused), a
  10 s `statement_timeout`, constrained `work_mem`, and a 500-row cap with truncation disclosure.
- **Repair, bounded.** A failed SQL surfaces the error text to the model for correction, at most two
  attempts; beyond that the exchange ends as a refusal ("I couldn't answer that"), never a partial
  or fabricated answer.
- **Measured, not hopeful.** The eval corpus — hand-derived (question → executed rows) pairs over the
  fixture database — gates merges at ≥90% execution accuracy and 100% on refusals; it doubles as the
  few-shot bank, so documented and tested semantics cannot drift apart.

## 3. The privacy boundary: explicit, audited, no fallback

The default provider is hosted (OpenCode Go): the question and the result rows transit the provider's
infrastructure. That is a documented acceptance, not an accident:

- **No silent fallback.** A provider outage or missing key produces a refusal, never a different
  provider behind the scenes and never a fabricated answer.
- **Auditable per exchange.** The Chat Log records provider, generated SQL, row count, and outcome
  for every question; thumbs make it a quality loop, and the corpus grows from it.
- **Local is one config flip.** Ollama (privacy default), Bosch BMF, or DeepSeek native are the same
  OpenAI-compatible seam; each adoption must pass its own corpus gate (ticket 04) before use.