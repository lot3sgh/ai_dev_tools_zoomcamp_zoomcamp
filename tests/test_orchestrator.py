"""Core smoke suite: the loop with a scripted stub — no network, no tools."""

from assistant_core import Orchestrator, Outcome, StubProvider, Tool, ToolResult


class _EchoTool(Tool):
    name = "echo"
    description = "echoes the SQL back"

    def docs(self, few_shots: str = "") -> str:
        return "echo schema: nothing"

    def run(self, sql: str) -> ToolResult:
        if "bad" in sql:
            return ToolResult(error="syntax error at or near 'bad'")
        return ToolResult(rows=[(sql,)], row_count=1)


def test_answer_action():
    out = Orchestrator(StubProvider(turns=[{"answer": "42"}]), _EchoTool()).answer("q?")
    assert out.text == "42"
    assert out.refusal is None and out.error is None


def test_sql_then_phrase_round_trip():
    out = Orchestrator(
        StubProvider(turns=[{"sql": "SELECT 1"}, {"answer": "one"}]), _EchoTool()
    ).answer("q?")
    assert out.text == "one"
    assert out.sql == "SELECT 1"
    assert out.row_count == 1


def test_repair_budget_consumed():
    out = Orchestrator(
        StubProvider(turns=[
            {"sql_err": "SELECT bad syntax"},
            {"sql": "SELECT ok"},
            {"answer": "repaired"},
        ]),
        _EchoTool(),
    ).answer("q?")
    assert out.text == "repaired"
    assert out.repairs == 1


def test_refusal_is_terminal():
    out = Orchestrator(
        StubProvider(turns=[{"refuse": "out of my surface"}]), _EchoTool()
    ).answer("q?")
    assert out.refusal == "out of my surface"
    assert out.text is None