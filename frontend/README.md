# Frontend — Health Assistant chat UI

A single-page mobile-friendly chat for the Health Assistant. Deliberately small:
**no build step, no framework, no external assets** — plain ES modules served by the
FastAPI backend from this directory (`GET /` → `index.html`, assets at `/css`, `/js`).

## Structure

| File | Responsibility |
|---|---|
| `index.html` | the page skeleton (one <main>, one footer) |
| `css/style.css` | all styling (dark, phone-first) |
| `js/api.js` | **the only module that talks to the backend** — `ask()` (SSE streaming) and `rate()` (thumbs); centralizes fetch, headers, error mapping (`OfflineError` on 503) |
| `js/sse.js` | the SSE wire parser (incremental decoder) — the only protocol knowledge |
| `js/app.js` | DOM glue only: message bubbles, chips for executed SQL, thumbs, offline banner, session id in `localStorage` |
| `tests/` | `node --test` unit tests for the parser + the API client (mock `fetch`) |

Rule: nothing outside `api.js` performs a `fetch`; nothing outside `sse.js` knows the
SSE framing — this is what keeps the frontend testable and the contract centralized.

## Backend contract (used, not guessed)

`openapi.yaml` at the repo root declares `/api/chat` (SSE: session/sql/token/outcome)
and `/api/feedback`; the app serves it at `/openapi.yaml` and its live spec at
`/openapi.json`. `index.html` targets exactly those endpoints.

## Run the tests

```bash
cd frontend
npm test            # node --test tests/*.test.mjs  (no dependencies at all)
```

The backend suite additionally verifies the page and its assets are served
(`tests/test_chat_service.py::test_home_serves_the_single_file_page_and_its_local_assets`).
CI runs the frontend tests in `.github/workflows/ci.yml`.