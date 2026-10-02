# Product Spec — Health Assistant

**One paragraph.** The Health Assistant is a LAN-only conversational layer over the
user's personal health data: you ask "how was my sleep last night?" in plain language,
the assistant writes read-only SQL over a curated Semantic Layer (four gold views +
`sleep_score`/`device`/`profile`), executes it as the read-only `chatbot` role under a
bounded execute contract, streams the answer to a single-page mobile UI, and logs every
exchange to the Chat Log. Out-of-surface questions are refused, never invented.
Correctness per provider is gated by a hand-derived eval corpus on every merge.

## Users & stories

1. **Data owner** — "how was my sleep last night?" per-night answers respecting the
   night grain; day-level steps/stress/HRV/SpO2/temperature; week trends; device and
   profile detail; operational (last sync); refusals for out-of-surface questions;
   honest "no data for that range"; token-by-token streaming; one UI from a phone.
2. **Operator** — the service joins the existing compose stack on the Linux host,
   LAN-only; the robot role is provisioned idempotently from env; restart preserves
   the Chat Log/grants/config, conversations are ephemeral; the nightdump covers the
   Chat Log.
3. **Developer/evaluator** — deterministic suite needs no network/key; the corpus
   gates merges (≥90% executed-correct, 100% refusals) and doubles as the few-shots;
   the generic core is reusable (own subtree in the monorepo).

## Functional requirements (selected)

- FR-1 Streaming chat endpoint: `POST /api/chat` → SSE events `session`/`token`/`sql`/
  `outcome`; terminal event carries outcome, row count, truncation, repairs, Chat Log id.
- FR-2 Execute contract: SELECT-only role; EXPLAIN dry-run + 1M-row guard; 10 s
  timeout; `work_mem` 32 MB; 500-row cap with disclosure; ≤2 self-repairs; refusal
  beats fabrication.
- FR-3 Semantic Layer comments (`COMMENT ON …`) are the model-facing semantics,
  versioned with DDL; the corpus is the few-shot bank.
- FR-4 Thumbs per exchange → `pipeline.chat_log.thumbs` (exactly once per row).
- FR-5 Provider seam: `LLM_PROVIDER=stub` (deterministic demo) | OpenAI-compatible
  `LLM_BASE_URL/MODEL/API_KEY`; no silent fallback; outage/missing-key = refusal.
- FR-6 Eval corpus: ≥20 pairs (per-night/day/week/detail/operational + refusals +
  empty-range), judged by executed result rows, order- and projection-insensitive;
  gate per provider before adoption.
- FR-7 Single-page UI: no build step, no framework, no external assets; centralized
  API client; protocol parser unit-tested.
- FR-8 MCP: `ask_health` + `semantic_layer_surface` over stdio, same engine.

## Non-functional

- Privacy: hosted provider sees question + result rows per exchange (documented,
  audited in Chat Log; no silent fallback); LAN is the access boundary in v1.
- Reproducibility: `docker compose up -d`; tests without network or keys.
- Observability: Chat Log, eval gate, ops probes (`ops/diagnosis.md`).

## Out of scope (v1)

RAG/embeddings (structured, small data); multi-user/auth/Internet exposure; native
apps/slack bots; pipeline internals beyond the operational view; automatic corpus
seeding (manual, from thumbed Chat Log rows).

## References

Spec & tickets: `.scratch/phase-3-health-assistant/issues/` · decision records:
`docs/adr/0006-…` (two-repo→monorepo + execute/privacy contract) · permissions:
`docs/permissions.md` · workflow: `docs/ai-workflow.md` · API: `openapi.yaml`.