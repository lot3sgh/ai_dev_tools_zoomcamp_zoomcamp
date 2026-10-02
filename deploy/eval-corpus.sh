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

# The key lives in the repo's .env (config.load_env reads it); mirror it into this shell
# so the skip check matches what `pipeline eval` actually sees.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "eval-corpus: LLM_API_KEY not set (.env or environment) — skipping the provider gate (deterministic suite unaffected)"
  exit 0
fi

echo "eval-corpus: running the golden corpus ($(grep -c 'CorpusPair(' backend/src/assistant/corpus.py) pairs) — provider gate"
uv run pipeline eval