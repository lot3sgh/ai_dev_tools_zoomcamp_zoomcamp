"""Phase 3 T3 — the provider factory at its env seam.

LLM_PROVIDER=stub must give a *corpus-keyed* deterministic provider (the ticket-02 demo:
corpus questions compute real rows from the fixture database, no key, no network); any
other LLM_PROVIDER value selects the OpenAI-compatible adapter from LLM_BASE_URL /
LLM_MODEL / LLM_API_KEY, and unusable configuration is a ProviderConfigError, not a
crash. The OpenAI-compatible adapter itself is covered in the core repo's adapter tests.
"""

from __future__ import annotations

import os

import pytest

from assistant_core import OpenAICompatProvider, StubProvider



def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


# ---------------------------------------------------------------- stub (demo) seam

@pytest.mark.integration
def test_stub_provider_answers_corpus_questions_from_the_fixture(reset_db, source_dir, monkeypatch):
    """LLM_PROVIDER=stub: a corpus question executes its expected SQL against the DB."""
    from assistant import corpus, providers

    monkeypatch.setenv("LLM_PROVIDER", "stub")
    assert _sync(source_dir) == 0
    stub = providers.build_provider()
    assert isinstance(stub, StubProvider)
    pair = next(p for p in corpus.load() if p.kind == "data")
    from assistant import engine

    out = engine.answer_question(pair.question, stub)
    assert out.sql == pair.expected_sql
    assert out.row_count == len(pair.expected_rows)


def test_stub_provider_falls_back_cleanly_for_unknown_questions(monkeypatch):
    from assistant import providers

    monkeypatch.setenv("LLM_PROVIDER", "stub")
    stub = providers.build_provider()
    out = "".join(stub.stream([{"role": "user", "content": "some unheard-of question?"}]))
    assert out.startswith("ANSWER:")
    assert "no script" in out


# ---------------------------------------------------------------- the OpenAI seam

def test_unconfigured_provider_is_a_clean_config_error(monkeypatch):
    from assistant import providers

    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(providers.ProviderConfigError):
        providers.build_provider()


def test_provider_seam_maps_env_to_the_openai_compatible_adapter(monkeypatch):
    from assistant import providers

    monkeypatch.setenv("LLM_BASE_URL", "http://gateway.example/v1")
    monkeypatch.setenv("LLM_MODEL", "opencode-go/deepseek-v4-flash")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_PROVIDER", "opencode-go")
    provider = providers.build_provider()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.model == "opencode-go/deepseek-v4-flash"
    assert provider.api_key == "secret"
    assert provider.name() == "opencode-go"


def test_missing_key_is_allowed_at_build_time_and_refuses_at_stream_time(monkeypatch):
    """No key: the adapter still builds — its stream() is a clean REFUSE (not a crash)."""
    from assistant import providers

    monkeypatch.setenv("LLM_BASE_URL", "http://gateway.example/v1")
    monkeypatch.setenv("LLM_PROVIDER", "opencode-go")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    provider = providers.build_provider()
    text = "".join(provider.stream([{"role": "user", "content": "q?"}]))
    assert text.startswith("REFUSE:")
    assert "LLM_API_KEY" in text