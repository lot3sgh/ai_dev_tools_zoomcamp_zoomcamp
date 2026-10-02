# Health Assistant — generic core

The provider-agnostic chatbot core: an orchestrator loop, a provider adapter seam, and a
capability-tools registry. Domain knowledge lives in **registered tools** owned by other
projects — this package knows none of them.

## The agent loop (v0.1, plain-text action protocol)

The orchestrator asks the provider for an action. The provider answers with exactly one of:

```
SQL:\n<sql>          → run the registered tool with <sql>
ANSWER:\n<text>      → final answer, nothing more to do
REFUSE:\n<text>      → final refusal
```

Loop: on `SQL`, execute the tool; on execution error, feed the error back and let the
provider repair (bounded); on success, hand the result rows back to the provider to phrase
the answer. That second round-trip — *result rows transit the provider* — is the privacy
boundary of the assistant, kept explicit here.

> The protocol is v0.1 for deterministic development. The real-provider adapter (Phase 3,
> ticket 03) maps function/tool calling onto the same `Query | Answer | Refuse` action
> surface so the loop itself does not change.

## Registration

A tool is a `name` + `description` + `run(...)` returning a `ToolResult` (rows, error,
refusal-reason). Projects import `register_tool` and provide their own `Tool`; see the
health pipeline repo for the run_health_query example.

## Development

```bash
uv sync --extra dev        # or: uv sync
uv run pytest              # core smoke suite (stub provider, no network)
```