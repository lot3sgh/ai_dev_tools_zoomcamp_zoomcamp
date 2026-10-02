// api.js — the *only* module that talks to the backend. The chat page calls ask()
// with handlers; the SSE wire format stays a parser concern in sse.js.
"use strict";

import { SseDecoder } from "./sse.js";

/** Raised when the assistant has no usable provider configuration (HTTP 503). */
export class OfflineError extends Error {
  constructor(detail) {
    super(detail || "assistant offline");
    this.name = "OfflineError";
  }
}

const JSON_HEADERS = { "Content-Type": "application/json" };

/**
 * Stream a question through POST /api/chat (event-stream response).
 * handlers: { onSession(id), onToken(chunk), onSql(meta), onOutcome(outcome) }
 * Resolves when the stream ends; rejects on transport errors.
 */
export async function ask(question, sessionId, handlers) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ question, session_id: sessionId }),
  });
  if (response.status === 503) {
    const error = await response.json().catch(() => ({}));
    throw new OfflineError(error.detail);
  }
  if (!response.ok) {
    throw new Error(`chat request failed: HTTP ${response.status}`);
  }
  if (!response.body) {
    throw new Error("no stream body");
  }

  const reader = response.body.getReader();
  const textDecoder = new TextDecoder();
  const decoder = new SseDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const decoded = textDecoder.decode(value, { stream: true });
    for (const event of decoder.feed(decoded)) {
      dispatch(event, handlers);
    }
  }
}

/** One thumbs rating per exchange (the backend enforces exactly-once). */
export async function rate(logId, thumbs) {
  const response = await fetch("/api/feedback", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ log_id: logId, thumbs }),
  });
  return response.ok;
}

function dispatch(event, handlers) {
  if (!handlers || !event.data) return;
  switch (event.name) {
    case "session":
      handlers.onSession?.(event.data.session_id);
      break;
    case "token":
      handlers.onToken?.(event.data.chunk);
      break;
    case "sql":
      handlers.onSql?.(event.data);
      break;
    case "outcome":
      handlers.onOutcome?.(event.data);
      break;
    default:
      break; // unknown events are ignored, never fatal
  }
}