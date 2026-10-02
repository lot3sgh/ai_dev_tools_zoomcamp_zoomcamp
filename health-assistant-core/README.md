# Health Assistant — generic core

The provider-agnostic chatbot core: an orchestrator loop, a provider adapter seam, and a
capability-tools registry. Domain knowledge lives in **registered tools** owned by other
projects — this package knows none of them.

## The agent loop (plain-text action protocol, streamed)

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

The loop is streaming: `Provider.stream(messages)` feeds every turn token-by-token, and
`Orchestrator.answer_stream(question, session)` yields `StreamEvent`s — `token` (the
user-visible text, live), `sql` (one event per *executed* tool query, with row count and
repair count; SQL never leaks as raw tokens), and a terminal `outcome`. `answer()` is the
synchronous shorthand that collects the stream.

## Providers

- `StubProvider` — deterministic scripted turns for tests: no network, no key.
- `OpenAICompatProvider` — a zero-dependency (stdlib) OpenAI chat-completions client
  (`stream: true`) for any compatible gateway: Ollama `/v1`, Bosch BMF, DeepSeek native,
  OpenCode Go — one config flip apart. Missing key, refused HTTP status, unreachable
  host, and malformed streams are each a distinct `REFUSE` (never a crash, never a
  silent fallback provider). `name()` reports the model id, so the Chat Log names who
  answered.

## Registration

A tool is a `name` + `description` + `run(...)` returning a `ToolResult` (rows, error,
refusal-reason). Projects import `register_tool` and provide their own `Tool`; see the
health pipeline repo for the run_health_query example.

## Development

```bash
uv sync --extra dev        # or: uv sync
uv run pytest              # core smoke suite (stub provider, no network)
```