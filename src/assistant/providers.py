"""Provider factory (Phase 3, ticket 03) — one config flip, no silent fallback.

`LLM_PROVIDER` selects the seam: `stub` for the deterministic demo provider (corpus
questions execute their expected SQL against the fixture database — no key, no network),
anything else for the OpenAI-compatible adapter configured by `LLM_BASE_URL` / `LLM_MODEL` /
`LLM_API_KEY` — Ollama, Bosch BMF, DeepSeek native, or OpenCode Go are one env block apart.

The privacy boundary is a documented acceptance, not an accident: with a hosted gateway the
question and the result rows transit the provider, and the Chat Log records which provider
answered (the adapter's name() is the configured model id). Missing configuration or a
provider outage produce a clean refusal — never a crash and never a different provider
behind the scenes (no silent local fallback).
"""

from __future__ import annotations

from assistant_core import OpenAICompatProvider, Provider, StubProvider

from assistant import corpus
from pipeline import config

# Default model for the OpenCode Go gateway. The wire model id is resolved from the
# gateway's model list (curl https://opencode.ai/zen/go/v1/models) — `deepseek-v4-flash`
# is verified there (2026-10-02). The `opencode-go/` prefix is OpenCode's own config
# namespace (`opencode-go/<model-id>`); the API itself takes the bare id. Never guess an
# id: a wrong one fails loudly at the gateway as a REFUSE, which is the designed outcome.
DEFAULT_MODEL = "deepseek-v4-flash"


class ProviderConfigError(RuntimeError):
    """Configuration is unusable: the assistant is down by design, not by accident."""


class CorpusStub(StubProvider):
    """Deterministic demo provider: corpus questions run their expected SQL for real.

    The ticket-02 demo with zero keys and zero network: type a corpus question into the
    UI and the streamed answer is computed from the fixture database by the real tool
    (chatbot role, all rails). A question with no corpus script answers a clear line —
    it never invents data. name() stays "stub" so the Chat Log shows which provider.
    """

    def __init__(self, pairs: list[corpus.CorpusPair] | None = None):
        self._by_question = {
            p.question: p for p in (pairs or corpus.load()) if p.question
        }

    def complete(self, messages: list[dict]) -> str:
        return "".join(self.stream(messages))

    def stream(self, messages: list[dict], *, session_id: str | None = None):
        last_content = messages[-1]["content"] if messages else ""
        if last_content.startswith(("The query returned", "Your SQL failed")):
            # phrasing / repair turn: the executed SQL already surfaced as a sql event
            yield "ANSWER:\nstub demo: deterministic provider — the SQL above ran against " \
                  "the database (set LLM_PROVIDER/BASE_URL/MODEL/API_KEY for a real model)."
            return
        pair = self._by_question.get(last_content)
        if pair is None:
            yield "ANSWER:\nstub demo: no script for this question — set LLM_PROVIDER, " \
                  "LLM_BASE_URL, LLM_MODEL and LLM_API_KEY for a real model."
            return
        if pair.kind == "refusal":
            yield f"REFUSE:\nstub demo: refused per the eval corpus ({pair.note})"
            return
        yield f"SQL:\n{pair.expected_sql}"


def build_provider() -> Provider:
    """Build the provider selected by the environment (raises ProviderConfigError)."""
    kind = config.llm_provider().lower()
    if kind == "stub":
        # Dev/demo seam: corpus questions compute answers from the fixture database.
        # Production never sets LLM_PROVIDER=stub — the default (empty) is the
        # OpenAI-compatible seam below.
        return CorpusStub()

    base_url = config.llm_base_url()
    if not base_url:
        raise ProviderConfigError(
            "LLM_BASE_URL is not set — point it at the OpenAI-compatible gateway "
            "(Ollama http://localhost:11434/v1, the BMF endpoint, or the OpenCode "
            "gateway). LLM_PROVIDER=stub runs the deterministic demo provider instead."
        )
    model = config.llm_model() or DEFAULT_MODEL
    return OpenAICompatProvider(
        base_url=base_url,
        model=model,
        api_key=config.llm_api_key(),
        provider_name=config.llm_provider() or None,
        user_agent="health-assistant/0.3",
    )