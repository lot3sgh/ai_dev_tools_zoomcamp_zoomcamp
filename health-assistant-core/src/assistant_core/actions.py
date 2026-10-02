from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Query:
    """The provider wants the registered tool executed with this SQL."""

    sql: str


@dataclass(frozen=True)
class Answer:
    """The provider's final answer; nothing more to do."""

    text: str


@dataclass(frozen=True)
class Refuse:
    """The provider refuses; a first-class outcome, never a fabricated answer."""

    text: str


@dataclass
class ToolResult:
    """What a registered tool returns after executing a Query."""

    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str | None = None          # execution failed -> orchestrator may repair
    refusal: str | None = None        # tool refused (e.g. unbounded query) -> terminal


@dataclass(frozen=True)
class Outcome:
    """The engine's final outcome for one question (also what the Chat Log records)."""

    text: str | None = None        # final phrased answer (None on failure)
    refusal: str | None = None        # set when refused
    error: str | None = None          # set when repairs were exhausted
    sql: str | None = None            # last SQL executed
    row_count: int = 0
    truncated: bool = False
    repairs: int = 0