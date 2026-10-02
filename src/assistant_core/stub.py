"""Deterministic scripted provider for tests — no network, no key.

Each question may consume a script of turns. A turn is one of:
  {"sql": "SELECT ..."}          → emits SQL:<sql>
  {"sql_err": "SELECT bad"}      → emits a *syntactically broken* SQL (drives repair)
  {"answer": "text"}             → emits ANSWER:<text>
  {"refuse": "text"}             → emits REFUSE:<text>
Turns are consumed in order; if the script runs out, the final turn repeats.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StubProvider:
    provider_name: str = "stub"
    turns: list[dict] | None = None

    def name(self) -> str:
        return self.provider_name

    def complete(self, messages: list[dict]) -> str:
        script = self.turns or [{"answer": "stub: no script configured"}]
        # The orchestrator alternates turns per question: call #n uses turn #n if present.
        # Track calls per session-leg approximately: count _every_ completion.
        n = getattr(self, "_calls", 0)
        self._calls = n + 1
        turn = script[min(n, len(script) - 1)]
        if "sql" in turn:
            return f"SQL:\n{turn['sql']}"
        if "sql_err" in turn:
            return f"SQL:\n{turn['sql_err']}"
        if "refuse" in turn:
            return f"REFUSE:\n{turn['refuse']}"
        return f"ANSWER:\n{turn['answer']}"