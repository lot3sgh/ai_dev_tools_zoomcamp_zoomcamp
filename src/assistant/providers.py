"""Provider factory (Phase 3, ticket 03) — one config flip, no silent fallback.

`LLM_PROVIDER` selects the seam: `stub` for the deterministic adapter (dev/tests), anything
else for the OpenAI-compatible adapter configured by `LLM_BASE_URL` / `LLM_MODEL` /
`LLM_API_KEY` — Ollama, Bosch BMF, DeepSeek native, or OpenCode Go are one env block apart.

The privacy boundary is a documented acceptance, not an accident: with a hosted gateway the
question and the result rows transit the provider, and the Chat Log records which provider
answered (the adapter's name() is the configured model id). Missing configuration or a
provider outage produce a clean refusal — never a crash and never a different provider
behind the scenes (no silent local fallback).
"""

from __future__ import annotations

from assistant_core import OpenAICompatProvider, Provider, StubProvider

from pipeline import config

# Default model id for the OpenCode Go subscription. Verify the exact id from the OpenCode
# Console model list before first use — never invent a model id; a wrong one fails loudly
# at the gateway as a REFUSE, which is the designed outcome.
DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"


class ProviderConfigError(RuntimeError):
    """Configuration is unusable: the assistant is down by design, not by accident."""


def build_provider() -> Provider:
    """Build the provider selected by the environment (raises ProviderConfigError)."""
    kind = config.llm_provider().lower()
    if kind == "stub":
        # Dev/test seam only: scripted, deterministic, no network. Production never sets
        # LLM_PROVIDER=stub — the default (empty) is the OpenAI-compatible seam below.
        return StubProvider()

    base_url = config.llm_base_url()
    if not base_url:
        raise ProviderConfigError(
            "LLM_BASE_URL is not set — point it at the OpenAI-compatible gateway "
            "(Ollama http://localhost:11434/v1, the BMF endpoint, or the OpenCode "
            "gateway). LLM_PROVIDER=stub runs the deterministic test provider instead."
        )
    model = config.llm_model() or DEFAULT_MODEL
    return OpenAICompatProvider(
        base_url=base_url,
        model=model,
        api_key=config.llm_api_key(),
        provider_name=config.llm_provider() or None,
    )