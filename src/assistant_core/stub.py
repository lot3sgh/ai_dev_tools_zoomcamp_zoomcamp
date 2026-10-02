"""Deterministic scripted provider for tests — no network, no key.

Each question may consume a script of turns. A turn is one of:
  {"sql": "SELECT ..."}          → emits SQL:<sql>
  {"sql_err": "SELECT bad"}      → emits a *syntactically broken* SQL (drives repair)
  {"answer": "text"}             → emits ANSWER:<text>
  {"refuse": "text"}             → emits REFUSE:<text>
Turns are consumed in order; if the script runs out, the final turn repeats.

complete() and stream() both consume from the same turn script, so a caller can mix
or use either; stream() shards the turn's text into fixed-size chunks (deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass


def chunk(text: str, size: int = 4) -> list[str]:
    """Shard text into size-char pieces (the deterministic streaming granularity)."""
    return [text[i : i + size] for i in range(0, len(text), size)]


@dataclass
class StubProvider:
    provider_name: str = "stub"
    turns: list[dict] | None = None

    def name(self) -> str:
        return self.provider_name

    def _next_turn(self) -> str:
        script = self.turns or [{"answer": "stub: no script configured"}]
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

    def complete(self, messages: list[dict]) -> str:
        return self._next_turn()

    def stream(self, messages: list[dict], *, session_id: str | None = None):
        for piece in chunk(self._next_turn()):
            yield piece