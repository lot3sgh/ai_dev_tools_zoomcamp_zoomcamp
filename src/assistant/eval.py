"""Eval runner + merge gate (Phase 3, ticket 04).

Judges the assistant the only way that means anything: by the *executed result rows* it
produces against the fixture database — never by SQL text. `run_corpus` drives the whole
loop (provider -> action -> real tool with all rails -> phrasing) per pair, records what
actually executed, and compares order-insensitively. The same machinery runs:

  - deterministically against the scripted stub (expected SQL per pair) — the mechanics
    check that needs no network and no key, and
  - as the provider gate (real provider from env) — the acceptance seam that blocks a
    merge below 90% execution accuracy or 100% on refusals.

The corpus is also consumed as few-shots by this runner, exactly as the service does.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from assistant_core import Outcome, Provider, Registry, StubProvider, Tool, ToolResult

from assistant import corpus
from assistant.tool import RunHealthQuery

DATA_THRESHOLD = 0.90   # >=90% execution accuracy on data/empty pairs
REFUSAL_THRESHOLD = 1.0  # 100% on refusals


# ------------------------------------------------------------------ normalization

def normalize(value):
    """Executed cell -> comparable literal (dates ISO, numerics float, None/bool kept)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    return str(value)


def rows_match(executed: list[tuple], expected: list[list]) -> bool:
    """Executed result rows vs expected literals, as order-insensitive multi-sets."""
    executed_set = Counter(tuple(normalize(v) for v in row) for row in executed)
    expected_set = Counter(tuple(normalize(v) for v in row) for row in expected)
    return executed_set == expected_set


# ------------------------------------------------------------------ the runner

class _RecordingTool:
    """Runs the real registered tool but keeps the executed rows for scoring."""

    def __init__(self, inner: Tool, executed: list):
        self._inner = inner
        self._executed = executed

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def description(self) -> str:
        return self._inner.description

    def docs(self, few_shots: str = "") -> str:
        return self._inner.docs(few_shots)

    def run(self, sql: str) -> ToolResult:
        result = self._inner.run(sql)
        if result.rows:
            self._executed.extend(result.rows)
        return result


@dataclass
class PairResult:
    pair: corpus.CorpusPair
    ok: bool
    outcome: Outcome | None = None
    executed_rows: list = field(default_factory=list)
    detail: str = ""


@dataclass
class EvalReport:
    results: list[PairResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def data_pairs(self) -> list[PairResult]:
        return [r for r in self.results if r.pair.kind in ("data", "empty")]

    @property
    def refusal_pairs(self) -> list[PairResult]:
        return [r for r in self.results if r.pair.kind == "refusal"]

    @property
    def data_accuracy(self) -> float:
        passed = sum(1 for r in self.data_pairs if r.ok)
        return passed / len(self.data_pairs) if self.data_pairs else 1.0

    @property
    def refusal_accuracy(self) -> float:
        passed = sum(1 for r in self.refusal_pairs if r.ok)
        return passed / len(self.refusal_pairs) if self.refusal_pairs else 1.0

    @property
    def failures(self) -> list[PairResult]:
        return [r for r in self.results if not r.ok]


def score_pair(pair: corpus.CorpusPair, executed_rows: list, outcome: Outcome) -> PairResult:
    if pair.kind == "refusal":
        detail = "" if outcome.refusal else f"answered instead of refusing: {outcome.text!r}"
        return PairResult(pair, outcome.refusal is not None, outcome, executed_rows, detail)
    answered = outcome.text is not None
    if pair.kind == "empty":
        ok = answered and not executed_rows
        detail = "" if ok else (
            "refused/errored" if not answered else f"rows returned {executed_rows!r}")
        return PairResult(pair, ok, outcome, executed_rows, detail)
    matched = rows_match(executed_rows, pair.expected_rows)
    ok = answered and matched
    detail = "" if ok else (
        "refused/errored" if not answered else
        f"rows mismatch: expected {pair.expected_rows}, got {executed_rows!r}")
    return PairResult(pair, ok, outcome, executed_rows, detail)


ProviderFactory = Callable[[corpus.CorpusPair], Provider]


def stub_provider_factory() -> ProviderFactory:
    """Deterministic path: the pair's expected SQL is scripted into the stub."""

    def factory(pair: corpus.CorpusPair) -> Provider:
        if pair.kind == "refusal":
            return StubProvider(turns=[{"refuse": "refused per corpus expectation"}])
        return StubProvider(turns=[{"sql": pair.expected_sql}, {"answer": "ok"}])

    return factory


def run_corpus(
    provider_factory: ProviderFactory,
    pairs: list[corpus.CorpusPair] | None = None,
    *,
    few_shots: str | None = None,
    timeout_s: float = 10,
    row_cap: int = 500,
    cardinality_guard: int = 1_000_000,
) -> EvalReport:
    from assistant_core import Orchestrator

    from assistant import register_health_tools

    pairs = pairs or corpus.load()
    few_shots = few_shots if few_shots is not None else corpus.few_shots()
    registry = register_health_tools(
        Registry(), timeout_s=timeout_s, row_cap=row_cap, cardinality_guard=cardinality_guard)
    inner = registry.get("run_health_query")
    assert inner is not None

    results: list[PairResult] = []
    for pair in pairs:
        executed: list[tuple] = []
        recorder = _RecordingTool(inner, executed)
        orch = Orchestrator(provider_factory(pair), recorder, few_shots=few_shots)
        outcome = orch.answer(pair.question)
        results.append(score_pair(pair, executed, outcome))
    return EvalReport(results)


@dataclass(frozen=True)
class PairSumm:
    data_pass: int
    data_total: int
    refusal_pass: int
    refusal_total: int


def gate_verdict(summ: PairSumm) -> tuple[bool, str]:
    data_acc = summ.data_pass / summ.data_total if summ.data_total else 1.0
    ok_data = data_acc >= DATA_THRESHOLD
    ok_refusal = summ.refusal_pass == summ.refusal_total
    if ok_data and ok_refusal:
        return True, f"gate passed: {data_acc:.0%} data ({summ.data_pass}/{summ.data_total}), " \
                     f"{summ.refusal_pass}/{summ.refusal_total} refusals"
    problems = []
    if not ok_data:
        problems.append(f"execution accuracy {data_acc:.0%} < {DATA_THRESHOLD:.0%}")
    if not ok_refusal:
        problems.append(f"refusal accuracy {summ.refusal_pass}/{summ.refusal_total} "
                        f"< {REFUSAL_THRESHOLD:.0%}")
    return False, "gate failed: " + "; ".join(problems)


def report_summary(report: EvalReport) -> PairSumm:
    return PairSumm(
        data_pass=sum(1 for r in report.data_pairs if r.ok),
        data_total=len(report.data_pairs),
        refusal_pass=sum(1 for r in report.refusal_pairs if r.ok),
        refusal_total=len(report.refusal_pairs),
    )


# ------------------------------------------------------------------ the gate entry

def print_report(report: EvalReport) -> None:
    summ = report_summary(report)
    print(f"corpus: {report.total} pairs | data {summ.data_pass}/{summ.data_total} "
          f"({report.data_accuracy:.0%}) | refusals {summ.refusal_pass}/{summ.refusal_total}")
    for failure in report.failures:
        print(f"  FAIL: {failure.pair.question!r} [{failure.pair.kind}] {failure.detail}")


def gate_skip(reason: str) -> int:
    print(f"eval: skipping the provider gate ({reason})")
    return 0


# ------------------------------------------------------------------ the gate entry

def fixture_database_name() -> str:
    """Scratch database the corpus fixture runs in (never the real data)."""
    return "health_pipeline_eval"


def run_gate(
    evaluate_with: ProviderFactory,
    *,
    db_name: str | None = None,
    pairs: list[corpus.CorpusPair] | None = None,
) -> EvalReport:
    """Fresh fixture DB + run the corpus through `evaluate_with` (stub or real provider).

    Idempotent: the scratch database is dropped and recreated on every run, so re-running
    the gate is repeatable with identical outcomes (the repo's rerun discipline).
    """
    from pipeline import config, db

    db_name = db_name or fixture_database_name()
    _fresh_database(db_name)

    import tempfile
    from pathlib import Path

    from pipeline.cli import main as cli_main

    with tempfile.TemporaryDirectory() as tmp:
        from assistant.fixtures import build_fixture_zip

        build_fixture_zip(Path(tmp))
        os.environ["PGDATABASE"] = db_name
        try:
            if cli_main(["sync", "--source", "local", "--path", tmp]) != 0:
                raise RuntimeError("fixture sync failed — gate aborted")
            # The corpus runs through the same tool (chatbot role, all rails) and the
            # same execute contract as the live service — judged at the executed-row seam.
            return run_corpus(evaluate_with, pairs=pairs)
        finally:
            os.environ.pop("PGDATABASE", None)


def _fresh_database(db_name: str) -> None:
    """Drop + recreate the scratch database (superuser pipeline can create databases)."""
    import psycopg

    from pipeline import config

    maintenance = psycopg.conninfo.make_conninfo(**{
        **{k: config.db_env()[k] for k in ("host", "port", "user", "password")},
        "dbname": "postgres",
    })
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
        conn.execute(f"CREATE DATABASE {db_name}")