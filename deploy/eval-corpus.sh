#!/usr/bin/env bash
# Eval-corpus merge gate (Phase 3, ticket 04).
#
# Runs the golden corpus against the provider selected by the environment and exits
# non-zero when the thresholds are missed (>=90% executed-correct on data/empty pairs,
# 100% on refusals) — that is the merge blocker. Skips cleanly when no LLM_API_KEY is
# present so the deterministic suite and provider-less CI are never blocked by it.
#
# Wire this as the pre-merge/CI job (or a check before `git merge`): it uses the .env
# of the repo (or the caller's environment) for LLM_* and CHATBOT_DB_PASSWORD.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "eval-corpus: LLM_API_KEY not set — skipping the provider gate (deterministic suite unaffected)"
  exit 0
fi

echo "eval-corpus: running the golden corpus ($(grep -c '"question"' src/assistant/corpus.py) pairs) — provider gate"
uv run pipeline eval