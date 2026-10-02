import test from "node:test";
import assert from "node:assert/strict";

import { ask, rate, OfflineError } from "../js/api.js";

function sseResponse(body, status = 200) {
  return new Response(body, { status });
}

test("ask() dispatches session/token/outcome events from a streamed response", async () => {
  globalThis.fetch = async () =>
    sseResponse(
      'event: session\ndata: {"session_id":"s1"}\n\n' +
        'event: sql\ndata: {"sql":"SELECT 1","rows":1,"truncated":false,"repairs":0}\n\n' +
        'event: token\ndata: {"chunk":"An"}\n\n' +
        'event: token\ndata: {"chunk":"swer"}\n\n' +
        'event: outcome\ndata: {"outcome":"answered","log_id":7,"row_count":1}\n\n'
    );

  const events = [];
  await ask("question?", "s1", {
    onSession: (id) => events.push(["session", id]),
    onToken: (chunk) => events.push(["token", chunk]),
    onSql: (meta) => events.push(["sql", meta.rows]),
    onOutcome: (result) => events.push(["outcome", result.log_id]),
  });
  assert.deepEqual(events, [
    ["session", "s1"],
    ["sql", 1],
    ["token", "An"],
    ["token", "swer"],
    ["outcome", 7],
  ]);
});

test("ask() rejects with OfflineError when the service has no provider", async () => {
  globalThis.fetch = async () =>
    sseResponse('{"detail":"assistant offline: LLM_BASE_URL is not set"}', 503);
  await assert.rejects(
    () => ask("hi?", "s1", {}),
    (error) => error instanceof OfflineError && error.message.includes("LLM_BASE_URL")
  );
});

test("ask() rejects on transport errors", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("connection refused");
  };
  await assert.rejects(() => ask("hi?", "s1", {}), TypeError);
});

test("unknown events are ignored and never fatal", async () => {
  globalThis.fetch = async () =>
    sseResponse('event: something-new\ndata: {"x":1}\n\nevent: token\ndata: {"chunk":"ok"}\n\n');
  const tokens = [];
  await ask("q?", "s1", { onToken: (chunk) => tokens.push(chunk) });
  assert.deepEqual(tokens, ["ok"]);
});

test("rate() resolves true on success and false on rejection", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: true }), { status: 200 });
  assert.equal(await rate(7, "up"), true);
  globalThis.fetch = async () => new Response("nope", { status: 404 });
  assert.equal(await rate(7, "up"), false);
});