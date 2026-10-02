"""Health Assistant — the health-side registration for the generic core (Phase 3).

The generic orchestrator/provider/registry live in the health-assistant-core package; this
package registers the run_health_query tool over the Semantic Layer and owns the Chat Log.
"""

from assistant.engine import (
    answer_question,
    chatbot_conninfo,
    make_tool,
    register_health_tools,
)
from assistant.tool import RunHealthQuery

__all__ = [
    "RunHealthQuery",
    "answer_question",
    "chatbot_conninfo",
    "make_tool",
    "register_health_tools",
]