"""The generic agent loop: question -> provider actions -> tool -> phrased answer.

Tool-agnostic: the registered Tool carries name/description/docs/run. The loop owns the
repair budget and the result-phrasing round-trip (the privacy-relevant path where result
rows go back to the provider).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from assistant_core.actions import Outcome, ToolResult
from assistant_core.provider import Provider, build_system_prompt


class Tool(Protocol):
    name: str
    description: str

    def docs(self, few_shots: str = "") -> str:
        ...

    def run(self, sql: str) -> ToolResult:
        ...


@dataclass
class Orchestrator:
    provider: Provider
    tool: Tool
    max_repairs: int = 2

    def _parse(self, raw: str) -> tuple[str, str]:
        """Return (kind, payload) from the action protocol text."""
        for kind in ("SQL", "ANSWER", "REFUSE"):
            prefix = kind + ":\n"
            if raw.strip().startswith(prefix):
                return kind, raw.strip()[len(prefix) :].strip()
            if raw.strip() == kind + ":":
                return kind, ""
        return "ANSWER", raw.strip()

    def answer(self, question: str, session: list[dict] | None = None) -> Outcome:
        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(
                self.tool.name, self.tool.description, self.tool.docs(), "")},
        ]
        if session:
            messages.extend(session)
        messages.append({"role": "user", "content": question})

        repairs = 0
        while True:
            raw = self.provider.complete(messages)
            kind, payload = self._parse(raw)
            if kind == "ANSWER":
                return Outcome(text=payload, repairs=repairs)
            if kind == "REFUSE":
                return Outcome(refusal=payload, repairs=repairs)
            if kind == "SQL":
                result = self.tool.run(payload)
                if result.refusal:
                    return Outcome(refusal=result.refusal, sql=payload,
                                   row_count=result.row_count, repairs=repairs)
                if result.error and repairs < self.max_repairs:
                    repairs += 1
                    messages.append({"role": "user", "content":
                        f"Your SQL failed: {result.error}. Reply with corrected SQL, or REFUSE."})
                    continue
                if result.error:
                    return Outcome(error=result.error, sql=payload,
                                   row_count=result.row_count, repairs=repairs)
                # Success: phrase the result through the provider (results transit it).
                messages.append({"role": "user", "content":
                    f"The query returned {result.row_count} row(s)"
                    f"{' (truncated at the cap)' if result.truncated else ''}:\n"
                    f"{_render(result)}"})
                phrased = self._parse(self.provider.complete(messages))
                if phrased[0] == "REFUSE":
                    return Outcome(refusal=phrased[1], sql=payload,
                                   row_count=result.row_count, truncated=result.truncated,
                                   repairs=repairs)
                return Outcome(text=phrased[1], sql=payload, row_count=result.row_count,
                               truncated=result.truncated, repairs=repairs)


def _render(result: ToolResult) -> str:
    return "\n".join(
        "\t".join(str(v) for v in row)[:400] for row in result.rows[:50]
    )