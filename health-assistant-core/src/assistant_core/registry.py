"""Capability-tools registry (Phase 4 seam).

Projects register their own Tools onto a Registry; the generic core never knows them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assistant_core.orchestrator import Tool


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return sorted(self.tools)