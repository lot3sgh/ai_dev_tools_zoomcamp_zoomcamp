"""run_health_query — the Health Assistant's single tool (Phase 3, ticket 01).

Executes model-written SQL against the Semantic Layer as the read-only `chatbot` role.
Owns the execute contract: EXPLAIN dry-run + cardinality guard, statement timeout, row cap
with disclosure. Semantics come from the database (COMMENT ON the layer) via docs().
"""

from __future__ import annotations

import json

import psycopg
from psycopg import sql as psql

from assistant_core import ToolResult

# The Semantic Layer: exactly what provision_chatbot_role grants. Docs are fetched from the
# DB comments; this list names the relations the tool is allowed to talk about.
SURFACE: dict[str, list[str]] = {
    "gold": ["daily_health", "sleep_summary", "activity_trends", "freshness"],
    "silver": ["sleep_score", "device", "profile"],
}


class RunHealthQuery:
    """One Tool registration: name/description for the provider, run() for execution."""

    name = "run_health_query"
    description = (
        "Runs read-only SQL over the health Semantic Layer (gold views + sleep_score/device/"
        "profile). Use it for any health-data question. Never reference other relations."
    )

    def __init__(
        self,
        chatbot_conn: str,
        *,
        timeout_s: float = 10,
        row_cap: int = 500,
        cardinality_guard: int = 1_000_000,
    ):
        self._conninfo = chatbot_conn
        self.timeout_s = timeout_s
        self.row_cap = row_cap
        self.cardinality_guard = cardinality_guard

    # -- model-facing documentation ------------------------------------------------

    def docs(self, few_shots: str = "") -> str:
        """Grain documentation from the database comments — the single source of truth."""
        entries: list[str] = []
        with psycopg.connect(self._conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                for schema, relations in SURFACE.items():
                    cur.execute(
                        """SELECT c.relname, d.description
                           FROM pg_class c
                           JOIN pg_namespace n ON n.oid = c.relnamespace
                           JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = 0
                           WHERE n.nspname = %s AND c.relname = ANY(%s)
                           ORDER BY c.relname""",
                        (schema, relations),
                    )
                    for relname, description in cur.fetchall():
                        entries.append(f"{schema}.{relname}: {description}")
        if not entries:
            return few_shots
        return "Acceptable relations and their semantics:\n" + "\n".join(entries)
        + ("\n\n" + few_shots if few_shots else "")

    # -- execution -----------------------------------------------------------------

    def run(self, raw_sql: str) -> ToolResult:
        sql = raw_sql.strip().rstrip(";").strip()
        if not sql:
            return ToolResult(error="empty SQL")

        with psycopg.connect(self._conninfo) as conn:
            # Dry-run: syntax/columns checked without executing; cardinality guarded.
            try:
                est = self._estimate(conn, sql)
            except psycopg.Error as exc:
                return ToolResult(error=_short(str(exc.__cause__ or exc)))
            if est is not None and est > self.cardinality_guard:
                return ToolResult(
                    refusal=f"refused: query would scan an estimated {est:,} rows; "
                            "add a WHERE on a real time range."
                )
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = {int(self.timeout_s * 1000)}")
                    cur.execute("SET work_mem = '32MB'")
                    cur.execute(sql)
                    columns = [d.name for d in cur.description] if cur.description else []
                    fetched = cur.fetchmany(self.row_cap + 1) if columns else []
            except psycopg.errors.QueryCanceled as exc:
                return ToolResult(error=f"query canceled after {self.timeout_s:.0f}s: "
                                        f"{_short(str(exc.__cause__ or exc))}")
            except psycopg.Error as exc:  # incl. permission/relation errors → tool surface
                return ToolResult(error=_short(str(exc.__cause__ or exc)))

        rows = fetched[: self.row_cap]
        truncated = len(fetched) > self.row_cap
        return ToolResult(
            columns=[str(c) for c in columns],
            rows=[tuple(r) for r in rows],
            row_count=len(rows),
            truncated=truncated,
        )

    def _estimate(self, conn: psycopg.Connection, sql: str) -> int | None:
        with conn.cursor() as cur:
            cur.execute(psql.SQL("EXPLAIN (FORMAT JSON) {}").format(psql.SQL(sql)))
            row = cur.fetchone()
            raw = row[0] if row else "[]"
        # psycopg parses the json column into a list; tolerate a raw string too.
        plans = json.loads(raw) if isinstance(raw, str) else raw
        plan = plans[0]["Plan"]

        def rows(node: dict) -> int:
            best = node.get("Plan Rows", 0)
            return max([int(best)] + [rows(ch) for ch in node.get("Plans", [])])

        return rows(plan)


def _short(msg: str) -> str:
    """First meaningful line of a Postgres error, for the repair loop."""
    return msg.split("\n")[0].strip()[:300]