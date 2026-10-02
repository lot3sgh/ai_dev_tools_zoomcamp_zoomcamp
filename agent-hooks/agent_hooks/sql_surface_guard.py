"""Agent hook: the lexical SQL-surface guard for run_health_query.

The execute contract's PRIMARY enforcement is the chatbot role's grants — bronze and the
high-volume silver families are unreachable by construction (ADR-0006). This hook is the
tool-layer guardrail in front of the EXPLAIN dry-run: it refuses multi-statement input,
non-read statements, and references to relations outside the Semantic Layer before a
single byte reaches the executor, so even a misconfigured role, a leaked grant, or a
hostile prompt cannot turn the assistant into a writer or an exfiltrator.

This module is the single source of truth for the guard semantics; RunHealthQuery.run()
calls guard_sql() first (see agent-hooks/sql_surface_guard_test.py for the contract).
"""

from __future__ import annotations

import re

# The Semantic Layer — must stay in lockstep with assistant.tool.SURFACE (the role
# grants are the enforcement; this list is the prompt-independent mirror).
GOLD_VIEWS = frozenset({"daily_health", "sleep_summary", "activity_trends", "freshness"})
SILVER_TABLES = frozenset({"sleep_score", "device", "profile"})

_READ_LEADERS = frozenset({"select", "with", "explain", "show"})
_DISALLOWED_LEADERS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "copy", "call", "vacuum", "comment", "do", "analyze",
})

_RELATION = re.compile(r"\b(gold|silver|bronze)\.([a-z_][a-z0-9_]*)")

GuardResult = tuple[bool, str]


def guard_sql(sql: str) -> GuardResult:
    """Validate one statement; returns (ok, reason). A refusal reason on failure."""
    sql = sql.strip()
    if not sql:
        return False, "empty SQL"

    if ";" in sql.rstrip() and any(part.strip() for part in sql.rstrip().split(";")[1:]):
        return False, "multi-statement input is refused (one statement per call)"

    first_word = sql.split(None, 1)[0].split("(", 1)[0].lower()
    if first_word in _DISALLOWED_LEADERS:
        return False, f"non-read statement refused: {first_word}"
    if first_word not in _READ_LEADERS:
        return False, f"statement must start with a read (SELECT/WITH); got {first_word!r}"

    for schema, relation in _RELATION.findall(sql.lower()):
        if schema == "bronze":
            return False, f"bronze.{relation} is outside the Semantic Layer"
        if schema == "silver" and relation not in SILVER_TABLES:
            return False, f"silver.{relation} is outside the Semantic Layer"
        if schema == "gold" and relation not in GOLD_VIEWS:
            return False, f"gold.{relation} is outside the Semantic Layer"

    return True, ""


def surface_docs() -> str:
    """The readable surface, for agent context and operator verification."""
    return (f"gold views: {', '.join(sorted(GOLD_VIEWS))}\n"
            f"whitelisted silver: {', '.join(sorted(SILVER_TABLES))}\n"
            "bronze and the high-volume silver families: unreachable by grants and refused "
            "by this guard")