# AI tool & data policy

What AI tooling is allowed in this project, who may answer with it, and what data can
cross which boundary. This policy is enforced by the repository's own mechanisms
(permissions doc, chat log, eval gate); the policy text is the operator's commitment.

## Allowed AI tooling

| Purpose | Tool | Boundary |
|---|---|---|
| Coding assistance (development) | any coding agent (OpenCode, pi-style agents, Claude Code) | runs the project's commands, proposes diffs; human approves merges |
| The product's LLM (answering health questions) | the OpenAI-compatible gateway from env (default OpenCode Go; Ollama/BMF/DeepSeek one flip apart) | sees the question + result rows (see §Data) |
| Automated review/audit | the repo's local scan commands (bandit, pip-audit, gitleaks), the eval corpus gate, two-axis human review | output is verifiable artifacts in `security/` |

## Data classification & flow

- **Your health data** (bronze/silver/gold, ~11M rows) lives in Postgres inside the
  home network. The only consumers with read rights are the read-only roles
  (`chatbot`, `dashboard`) plus the pipeline owner.
- **The hosted provider sees, per answered question:** the question text and the
  result rows of the executed query (needed to phrase an answer). That is the
  documented privacy boundary of ADR-0006 — explicit, audited, no silent fallback.
  The Chat Log records provider + question + SQL + outcome per exchange.
- **The Chat Log** (`pipeline.chat_log`) is append-only for the assistant, feeds the
  eval corpus's growth pool, and is included in the nightly backup.

## Rules

1. No health data leaves the LAN except through the configured provider (question +
   result rows per exchange) — never via logs to third-party SaaS, never embedded in
   issues/PRs, never in README examples (all samples are synthetic fixture data).
2. No `.env`, SA key, or real data sample may be committed; gitleaks runs against
   every commit so this is enforced, not promised.
3. No silent provider fallback. An outage, missing key, or config error is a refusal
   or an explicit offline state — a non-answer beats a wrong answer on health data.
4. Provider changes (Ollama local included) must pass the corpus gate before use
   (≥90% executed-correct, 100% refusals).
5. Human review precedes merges: the two-axis review (spec + standards) is the
   documented step for AI-assisted changes.

## Local-first option (privacy preference)

Ollama (or any local OpenAI-compatible server) is the one-config privacy default:
`LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=<your model>`, no key. Questions
and result rows then stay inside the network. Every provider is audited per exchange
in the Chat Log either way. Retention: the Chat Log and backups keep exchanges; the
user can delete rows by SQL if they wish (the assistant itself cannot).