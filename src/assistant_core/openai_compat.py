"""OpenAI-compatible provider adapter (Phase 3, ticket 03).

Speaks the OpenAI Chat Completions HTTP protocol (stream: true) to any compatible
gateway — Ollama (/v1), Bosch BMF, DeepSeek native, OpenCode Go — selectable purely by
configuration. stdlib only: the core repo has zero runtime dependencies, and the
adapter's tests point it at a local stub gateway (no network, no key).

The adapter relays the model's *text*; the orchestrator parses the plain-text action
protocol (SQL:/ANSWER:/REFUSE:). There is no native tool-calling here: the text protocol
is the locked design — the tool contract injects DB grain comments + few-shots at prompt
time (spec decision 5/10), which is what makes Ollama and every OpenAI-compatible host
drop-in, not just the ones with function-calling.

Failures are first-class and distinguishable, never a crash and never a silent different
provider: a missing key, a refused HTTP status, an unreachable host, and a malformed
stream each surface as a REFUSE with the reason in the text. name() reports the configured
model id (or provider label) so the Chat Log names exactly who answered.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator

DEFAULT_CHAT_PATH = "/chat/completions"

_REFUSE = "REFUSE:\n{}"


def _strip_trailing_slash(url: str) -> str:
    return url.rstrip("/")


@dataclass
class OpenAICompatProvider:
    base_url: str                 # e.g. https://gateway.example/v1 or http://localhost:11434/v1
    model: str                    # e.g. opencode-go/deepseek-v4-flash
    api_key: str | None = None    # None -> clean refusal at stream time (no silent fallback)
    provider_name: str | None = None   # Chat Log label; defaults to the model id
    timeout_s: float = 20.0
    temperature: float = 0.0
    chat_path: str = DEFAULT_CHAT_PATH
    # Gateways (e.g. OpenCode Go) expect a client's own user agent, not a generic
    # http-library name, for routing and abuse monitoring.
    user_agent: str = "assistant-core/0.1"

    def name(self) -> str:
        return self.provider_name or self.model

    # -- public Provider seam ------------------------------------------------------

    def complete(self, messages: list[dict]) -> str:
        return "".join(self.stream(messages))

    def stream(self, messages: list[dict]) -> Iterator[str]:
        if not self.api_key:
            yield _REFUSE.format(
                "LLM_API_KEY is not set — refusing with no fallback. "
                "There is no silent local provider; configure LLM_API_KEY."
            )
            return

        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": self.temperature,
        }
        url = _strip_trailing_slash(self.base_url) + self.chat_path
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout_s)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(200).decode("utf-8", "replace")
            except OSError:
                detail = ""
            yield _REFUSE.format(
                f"provider returned HTTP {exc.code} ({detail[:120]}); refusing rather "
                "than inventing an answer."
            )
            return
        except OSError as exc:
            yield _REFUSE.format(f"provider unreachable at {url}: {exc}")
            return

        if response.status != 200:
            text = b"".join(response).decode("utf-8", "replace")[:120]
            yield _REFUSE.format(
                f"provider returned HTTP {response.status} ({text}); refusing rather "
                "than inventing an answer."
            )
            return

        first_frame = True
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    frame = json.loads(data)
                except ValueError:
                    if first_frame:
                        yield _REFUSE.format(
                            "provider returned a malformed stream (expected SSE JSON); "
                            "check LLM_BASE_URL points at an OpenAI-compatible endpoint."
                        )
                        return
                    break  # late corruption: stop; the orchestrator repairs or errors
                first_frame = False
                choices = frame.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content")
                if content:
                    yield content
        except OSError as exc:
            raise RuntimeError(f"provider stream interrupted: {exc}") from exc