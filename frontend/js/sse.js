// sse.js — a tiny Server-Sent-Events parser (the only protocol knowledge the
// frontend has). Pure, dependency-free, unit-tested with node --test.
"use strict";

/** Parse one SSE block ("event: X\ndata: {...}") into { name, data } or null. */
export function parseSseBlock(block) {
  let name = null;
  let data = null;
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      name = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      try {
        data = JSON.parse(line.slice("data:".length).trim());
      } catch {
        data = null; // malformed payload: ignore the frame, keep the stream alive
      }
    }
  }
  return name ? { name, data } : null;
}

/**
 * Incremental decoder: feed chunks in, get completed events out. Blocks may be
 * split across network chunks; partial frames stay buffered until "\n\n".
 */
export class SseDecoder {
  constructor() {
    this.buffer = "";
  }

  feed(chunk) {
    this.buffer += chunk;
    const events = [];
    let idx;
    while ((idx = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const event = parseSseBlock(block);
      if (event) events.push(event);
    }
    return events;
  }
}