// app.js — the chat page: DOM glue over the api client. All backend calls go
// through api.js; this module holds no protocol or wire-format knowledge.
"use strict";

import { ask, rate, OfflineError } from "./api.js";

const chat = document.getElementById("chat");
const input = document.getElementById("q");
const send = document.getElementById("send");
const offline = document.getElementById("offline");
const SESSION_KEY = "health-assistant-session";

function sessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = "s-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function bubble(kind, text) {
  const el = document.createElement("div");
  el.className = "msg " + kind;
  el.textContent = text;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function chip(text) {
  const el = document.createElement("div");
  el.className = "sqlchip";
  el.textContent = text;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
}

function metaLine(logId, outcome) {
  const wrapper = document.createElement("div");
  wrapper.className = "meta";
  const badge = document.createElement("span");
  badge.className = "badge " + (outcome || "answered");
  badge.textContent =
    outcome === "refused" ? "refused" : outcome === "error" ? "error" : "answered";
  wrapper.appendChild(badge);
  if (logId != null && outcome === "answered") {
    const thumbs = document.createElement("span");
    thumbs.className = "thumbs";
    for (const dir of ["up", "down"]) {
      const button = document.createElement("button");
      button.textContent = dir === "up" ? "👍" : "👎";
      button.addEventListener("click", async () => {
        if (await rate(logId, dir)) {
          for (const other of thumbs.querySelectorAll("button")) {
            other.disabled = true;
            other.classList.toggle("on", other === button);
          }
        }
      });
      thumbs.appendChild(button);
    }
    wrapper.appendChild(thumbs);
  }
  chat.appendChild(wrapper);
  chat.scrollTop = chat.scrollHeight;
}

function caringCursor(answer) {
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  answer.appendChild(cursor);
  return cursor;
}

async function askNow(question) {
  send.disabled = true;
  bubble("user", question);
  const answer = bubble("assistant", "");
  let cursor = caringCursor(answer);
  let outcome = null;
  let logId = null;
  try {
    await ask(question, sessionId(), {
      onToken(chunk) {
        cursor.remove();
        answer.textContent += chunk;
        cursor = caringCursor(answer);
        chat.scrollTop = chat.scrollHeight;
      },
      onSql(sql) {
        chip(
          "⚙ " + sql.sql + "  → " + sql.rows + " row" + (sql.rows === 1 ? "" : "s") +
          (sql.truncated ? " (truncated)" : "") + (sql.repairs ? " [repaired]" : "")
        );
      },
      onOutcome(result) {
        outcome = result.outcome;
        logId = result.log_id;
      },
    });
  } catch (error) {
    if (error instanceof OfflineError) {
      offline.textContent = "Assistant offline — " + error.message;
      offline.classList.add("on");
      answer.textContent = "(offline)";
      return;
    }
    outcome = "error";
    answer.textContent = "Connection error — " + error.message;
  } finally {
    cursor.remove();
    offline.classList.remove("on");
    if (outcome) metaLine(logId, outcome);
    send.disabled = false;
    input.focus();
  }
}

send.addEventListener("click", () => {
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  askNow(question);
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter") send.click();
});
input.focus();