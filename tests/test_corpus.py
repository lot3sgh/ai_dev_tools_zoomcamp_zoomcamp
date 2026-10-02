"""Phase 3 T4 — the eval corpus: drift lock, scoring semantics, few-shots, gate math.

Seam: the corpus pairs are judged by *executed result rows* against the fixture database —
exactly the acceptance contract of the phase. Expected values below are hand-derived from
the synthetic takeout (src/assistant/fixtures.py), independent of any implementation.
"""

from __future__ import annotations

import json
import os

import psycopg

from assistant_core import StubProvider


from assistant import corpus, eval as evalmod  # noqa: E402
import pytest




def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _chatbot_conn(chatbot_password: str | None = None):
    """Connect as the chatbot role; the password follows config (see test_chat_engine)."""
    from pipeline import config

    password = chatbot_password or config.chatbot_password() or "test_chatbot_pw"
    kw = {k: config.db_env()[k] for k in ("dbname", "host", "port")}
    kw.update(user="chatbot", password=password)
    return psycopg.conninfo.make_conninfo(**kw)


def _exec(sql: str):
    """Execute the corpus's reference SQL through the real tool (chatbot role, all rails)."""
    from assistant import make_tool

    tool = make_tool(chatbot_conn=_chatbot_conn())
    result = tool.run(sql)
    assert result.error is None, f"reference SQL failed: {result.error}"
    assert result.refusal is None
    return result.rows


# ---------------------------------------------------------------- corpus shape

def test_corpus_reaches_the_target():
    pairs = corpus.load()
    assert len(pairs) >= 20
    kinds = {p.kind for p in pairs}
    assert {"data", "empty", "refusal"} <= kinds
    assert sum(1 for p in pairs if p.kind == "data") >= 15
    assert sum(1 for p in pairs if p.kind == "refusal") >= 4


def test_corpus_is_machine_consumable():
    """Pairs must survive JSON round-trip (a few-show bank, never code-bound types)."""
    payload = [p.__dict__ for p in corpus.load()]
    round_tripped = json.loads(json.dumps(payload))
    assert len(round_tripped) == len(payload)
    for pair in round_tripped:
        assert set(pair) == {"question", "kind", "expected_sql", "expected_rows", "note"}


# ---------------------------------------------------------------- drift lock

@pytest.mark.integration
def test_hand_derived_expected_values_agree_with_the_fixture(reset_db, source_dir):
    """The golden numbers were derived by hand — prove they are the fixture's numbers.

    This is the drift lock: if the fixture or the gold views ever change, this test breaks
    and the corpus is re-derived consciously, not silently.
    """
    assert _sync(source_dir) == 0
    for pair in corpus.load():
        if pair.kind == "refusal":
            continue
        executed = _exec(pair.expected_sql)
        assert evalmod.rows_match(executed, pair.expected_rows), (
            f"corpus pair drifted from fixture: {pair.question!r}\n"
            f"  expected {pair.expected_rows}\n  executed {executed}"
        )


# ---------------------------------------------------------------- scoring semantics

def test_scoring_is_order_insensitive():
    a = [(1, "x"), (2, "y")]
    b = [(2, "y"), (1, "x")]
    assert evalmod.rows_match(a, b)
    assert evalmod.rows_match(a, a)
    # duplicates are respected (two expected rows need two executed rows)
    assert not evalmod.rows_match([(1,), (1,)], [(1,)])


def test_scoring_ignores_extra_columns_but_not_extra_rows():
    """Judge semantics, not style: added context columns are fine; fabricated rows are not."""
    assert evalmod.rows_match([(1, "x", "extra")], [(1, "x")])
    assert evalmod.rows_match([("2026-09-20", 28)], [[28]])
    assert not evalmod.rows_match([(1,), (2,)], [(1,)])
    assert not evalmod.rows_match([(1, "x")], [(1, "x"), (2, "y")])


def test_scoring_detects_wrong_values():
    assert not evalmod.rows_match([(1, "x")], [(2, "x")])
    assert not evalmod.rows_match([(1, "x")], [])
    assert not evalmod.rows_match([], [(1, "x")])


@pytest.mark.integration
def test_stub_self_check_passes_every_pair(reset_db, source_dir):
    """The deterministic path: expected SQL scripted into the stub must yield the expected
    rows — the full loop, judged at the executed-row seam, no network, no key."""
    assert _sync(source_dir) == 0
    report = evalmod.run_corpus(evalmod.stub_provider_factory(), pairs=corpus.load())
    assert report.data_accuracy == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.failures == []
    assert report.total == len(corpus.load())


# ---------------------------------------------------------------- gate math

def test_gate_thresholds():
    ok, _ = evalmod.gate_verdict(evalmod.PairSumm(12, 12, 2, 2))   # 100% both
    assert ok
    ok, msg = evalmod.gate_verdict(evalmod.PairSumm(11, 12, 2, 2))  # 91.7% data, 100% refusals
    assert ok
    ok, msg = evalmod.gate_verdict(evalmod.PairSumm(10, 12, 2, 2))  # 83.3% -> below 90%
    assert not ok and "90" in msg
    ok, msg = evalmod.gate_verdict(evalmod.PairSumm(12, 12, 1, 2))  # one refusal missed
    assert not ok and "refusal" in msg


# ---------------------------------------------------------------- few-shots

def test_few_shots_are_prompt_ready():
    text = corpus.few_shots()
    assert "Q:" in text and "SQL:" in text
    first = corpus.load()[0]
    assert first.question.split("?")[0] in text  # the seed question is present


@pytest.mark.integration
def test_few_shots_reach_the_system_prompt(reset_db, source_dir):
    """The corpus doubles as the few-shot bank: the provider actually sees the examples."""
    seen: list[list[dict]] = []

    class _RecordingStub(StubProvider):
        def stream(self, messages, *, session_id=None):
            seen.append(messages)
            return iter([])  # empty stream -> empty answer

    assert _sync(source_dir) == 0
    from assistant import make_orchestrator

    orch = make_orchestrator(_RecordingStub(), few_shots=corpus.few_shots())
    orch.answer("any question")
    system = seen[0][0]["content"]
    assert "Q:" in system and "SQL:" in system
    assert corpus.load()[0].expected_sql in system