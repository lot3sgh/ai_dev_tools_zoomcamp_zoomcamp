"""The generic agent loop: question -> provider actions -> tool -> phrased answer.

Tool-agnostic: the registered Tool carries name/description/docs/run. The loop owns the
repair budget and the result-phrasing round-trip (the privacy-relevant path where result
rows go back to the provider).

The loop streams: every provider completion is consumed token-by-token (Provider.stream)
so the chat service can relay the user-visible text live over SSE. Action turns (SQL) are
never streamed — they are buffered and surface as a single executed-sql event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generator, Iterator, Protocol

from assistant_core.actions import Outcome, ToolResult
from assistant_core.provider import Provider, build_system_prompt


class Tool(Protocol):
    name: str
    description: str

    def docs(self, few_shots: str = "") -> str:
        ...

    def run(self, sql: str) -> ToolResult:
        ...


@dataclass(frozen=True)
class StreamEvent:
    """One event from the streaming loop. kind is one of: token | sql | outcome.

    token:   a chunk of the final (answer or refusal) text, in order.
    sql:     the SQL that was successfully *executed* by the tool (never the raw tokens).
    outcome: terminal — the full Outcome the Chat Log records.
    """

    kind: str
    text: str | None = None        # token chunks of the final text
    sql: str | None = None         # executed SQL (kind == 'sql')
    row_count: int = 0
    truncated: bool = False
    repairs: int = 0
    outcome: Outcome | None = None  # kind == 'outcome': terminal

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        short = self.text if self.text is not None else (
            self.sql if self.sql is not None else repr(self.outcome))
        return f"StreamEvent({self.kind}, {short!r})"


_HEADERS = ("SQL:", "ANSWER:", "REFUSE:")


def _decide(buffer: str) -> str:
    """Where a streamed buffer stands relative to the action headers.

    Returns 'sql' / 'answer' / 'refuse' once a full header leads the buffer,
    'pending' while the buffer is still a possible header prefix, else 'free'.
    """
    for header in _HEADERS:
        if buffer.startswith(header):
            return header[:-1].lower()
    for header in _HEADERS:
        if header.startswith(buffer):
            return "pending"
    return "free"


@dataclass
class Orchestrator:
    provider: Provider
    tool: Tool
    max_repairs: int = 2
    few_shots: str = ""

    def answer(self, question: str, session: list[dict] | None = None) -> Outcome:
        """Synchronous shorthand: collect the stream and return the terminal Outcome."""
        outcome: Outcome | None = None
        for event in self.answer_stream(question, session):
            if event.kind == "outcome":
                outcome = event.outcome
        assert outcome is not None, "stream ended without a terminal outcome"
        return outcome

    def answer_stream(
        self, question: str, session: list[dict] | None = None
    ) -> Iterator[StreamEvent]:
        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(
                self.tool.name, self.tool.description, self.tool.docs(), self.few_shots)},
        ]
        if session:
            messages.extend(session)
        messages.append({"role": "user", "content": question})

        repairs = 0
        last_sql, last_rows, last_truncated = None, 0, False
        kind: str
        payload: str
        while True:
            kind, payload = yield from self._stream_turn(messages)
            if kind == "ANSWER":
                yield StreamEvent(kind="outcome",
                                  outcome=Outcome(text=payload, repairs=repairs))
                return
            if kind == "REFUSE":
                yield StreamEvent(kind="outcome",
                                  outcome=Outcome(refusal=payload, repairs=repairs))
                return

            result = self.tool.run(payload)
            last_sql, last_rows, last_truncated = payload, result.row_count, result.truncated
            yield StreamEvent(kind="sql", sql=payload, row_count=result.row_count,
                              truncated=result.truncated, repairs=repairs)
            if result.refusal:
                yield StreamEvent(kind="outcome",
                                  outcome=Outcome(refusal=result.refusal, sql=payload,
                                                  row_count=result.row_count, repairs=repairs))
                return
            if result.error and repairs < self.max_repairs:
                repairs += 1
                messages.append({"role": "user", "content":
                    f"Your SQL failed: {result.error}. Reply with corrected SQL, or REFUSE."})
                continue
            if result.error:
                yield StreamEvent(kind="outcome",
                                  outcome=Outcome(error=result.error, sql=payload,
                                                  row_count=result.row_count, repairs=repairs))
                return

            # Success: phrase the result through the provider (results transit it).
            messages.append({"role": "user", "content":
                f"The query returned {result.row_count} row(s)"
                f"{' (truncated at the cap)' if result.truncated else ''}:\n"
                f"{_render(result)}"})
            kind, payload = yield from self._stream_turn(messages)
            if kind == "REFUSE":
                yield StreamEvent(kind="outcome",
                                  outcome=Outcome(refusal=payload, sql=last_sql,
                                                  row_count=last_rows, truncated=last_truncated,
                                                  repairs=repairs))
                return
            kind = "ANSWER"  # anything else after rows is the phrased answer
            yield StreamEvent(kind="outcome",
                              outcome=Outcome(text=payload, sql=last_sql,
                                              row_count=last_rows, truncated=last_truncated,
                                              repairs=repairs))
            return

    def _stream_turn(
        self, messages: list[dict]
    ) -> Generator[StreamEvent, None, tuple[str, str]]:
        """One provider completion.

        Streams the user-visible text as token events; returns the resolved
        (kind, payload) as the generator's value: SQL payloads are buffered whole
        (never token-streamed), ANSWER/REFUSE payloads stream as they arrive.
        """
        buffer = ""
        mode = "pending"      # pending | sql | answer | refuse | free
        payload: str = ""
        for chunk in self.provider.stream(messages):
            if mode == "pending":
                buffer += chunk
                mode = _decide(buffer)
                if mode == "pending":
                    continue
                if mode == "sql":
                    continue                    # keep buffering silently
                if mode in ("answer", "refuse"):
                    body = buffer[len(mode) + 1:]  # skip the header incl. ':'
                    body = body.lstrip("\n ")
                    payload += body
                    if body:
                        yield StreamEvent(kind="token", text=body)
                    continue
                # 'free': no header -> the whole buffer is answer text
                payload += buffer
                yield StreamEvent(kind="token", text=buffer)
            elif mode == "sql":
                buffer += chunk
            else:  # answer / refuse / free: relay as it arrives
                payload += chunk
                yield StreamEvent(kind="token", text=chunk)

        if mode == "pending":
            # Stream ended right at a header shape: 'ANSWER:' means empty answer;
            # a strict prefix such as 'ANSW' is free text.
            mode = _decide(buffer)
            if mode == "sql":
                return ("SQL", buffer[len("SQL:"):].strip())
            if mode in ("answer", "refuse"):
                return (mode.upper(), "")  # streaming already produced nothing
            payload += buffer  # strict-prefix leftovers count as free text
        if mode == "sql":
            return ("SQL", buffer[len("SQL:"):].strip())
        if mode in ("answer", "refuse"):
            return (mode.upper(), payload.strip())
        return ("ANSWER", payload.strip())


def _render(result: ToolResult) -> str:
    return "\n".join(
        "\t".join(str(v) for v in row)[:400] for row in result.rows[:50]
    )