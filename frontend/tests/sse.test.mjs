import test from "node:test";
import assert from "node:assert/strict";

import { parseSseBlock, SseDecoder } from "../js/sse.js";

test("parseSseBlock reads event name and JSON data", () => {
  const event = parseSseBlock('event: token\ndata: {"chunk":"hi"}');
  assert.deepEqual(event, { name: "token", data: { chunk: "hi" } });
});

test("parseSseBlock tolerates malformed JSON payloads", () => {
  const event = parseSseBlock('event: sql\ndata: not-json');
  assert.equal(event.name, "sql");
  assert.equal(event.data, null);
});

test("parseSseBlock returns null for empty blocks", () => {
  assert.equal(parseSseBlock(""), null);
  assert.equal(parseSseBlock(":\n"), null);
});

test("SseDecoder splits a complete stream into ordered events", () => {
  const decoder = new SseDecoder();
  const events = decoder.feed(
    'event: session\ndata: {"session_id":"s1"}\n\n' +
    'event: token\ndata: {"chunk":"hel"}\n\n' +
    'event: token\ndata: {"chunk":"lo"}\n\n' +
    'event: outcome\ndata: {"outcome":"answered"}\n\n'
  );
  assert.deepEqual(
    events.map((e) => e.name),
    ["session", "token", "token", "outcome"]
  );
  assert.equal(events[0].data.session_id, "s1");
});

test("SseDecoder reassembles frames split across chunks", () => {
  const decoder = new SseDecoder();
  const text = 'event: token\ndata: {"chunk":"hello world"}\n\n';
  const chunks = [text.slice(0, 7), text.slice(7, 25), text.slice(25)];
  let events = [];
  for (const chunk of chunks) events = events.concat(decoder.feed(chunk));
  assert.deepEqual(events, [{ name: "token", data: { chunk: "hello world" } }]);
});

test("SseDecoder ignores partial frames until a blank line arrives", () => {
  const decoder = new SseDecoder();
  assert.deepEqual(decoder.feed('event: token\ndata: {"chunk":"x'), []);
  assert.deepEqual(decoder.feed('"}\n\n'), [
    { name: "token", data: { chunk: "x" } },
  ]);
});