"""Provider adapter seam: the only boundary the orchestrator talks to.

T1 ships a deterministic StubProvider. The real OpenAI-compatible adapter (OpenCode Go /
BMF / Ollama) maps provider tool-calling onto the same action protocol so the loop never
changes. No provider-specific code lives outside this module's adapters.
"""

from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    """Text-in, text-out. The orchestrator parses the action protocol."""

    def complete(self, messages: list[dict]) -> str:
        ...

    def name(self) -> str:
        ...


def build_system_prompt(tool_name: str, tool_description: str, docs: str, few_shots: str) -> str:
    return (
        "You are the Health Assistant. You answer health-data questions by writing SQL you "
        "run through one registered tool; you never invent data you did not receive.\n\n"
        f"TOOL: {tool_name} — {tool_description}\n\n"
        "ACCEPTABLE SQL SURFACE (enforced by permissions, not by you):\n"
        f"{docs}\n\n"
        "Reply with exactly one of:\n"
        "SQL:\\n<sql>  — run this SQL through the tool\n"
        "ANSWER:\\n<text> — a final natural-language answer\n"
        "REFUSE:\\n<text> — you cannot answer (out of surface, or honestly unknown); "
        "never fabricate.\n\n"
        f"EXAMPLES (tested against this database):\n{few_shots}"
    )