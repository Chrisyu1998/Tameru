# Day 12 — SSE streaming + frontend reconnect

## Goal

Upgrade the chat wire from the non-streaming request/reply Day 10 ships on to Server-Sent Events. Convert `POST /chat/turn` to stream tokens. Frontend renders progressively (replacing Day 10's full-reply render). On stream drop, show a reconnect button.

## Read first

- `DESIGN.md` §7.1 (loop sketch with `yield sse_event`), §7.5 (streaming + reconnect failure mode).

## Deliverables

- Backend:
  - `POST /chat/turn` → returns `Content-Type: text/event-stream` via FastAPI's `StreamingResponse` wrapping a sync generator. The codebase is sync (Day 8 established this); `anthropic.Anthropic().messages.stream(...)` is a sync context manager that yields events, which composes naturally with a sync generator. **Do not switch to async here** — porting only the chat path to async would orphan it from the rest of the sync codebase. Set response headers `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` so Railway's edge proxy doesn't coalesce small chunks into bursts. The response body yields four event types:
    - `event: token\ndata: <text chunk>\n\n` — one per text token from Claude. **Stream tokens from every loop iteration, not just the final one.** Iteration-1 narration like "let me look that up…" flows into the same chat bubble as the final answer; matches Claude.ai / ChatGPT UX and avoids 4–6s of dead air during multi-hop turns.
    - `event: tool_use\ndata: {"name": "...", "input": {...}}\n\n` — emitted when the model starts a tool call. Drives the per-tool pill ("looking up dining transactions…"). The pill transitions to the next state when the next `tool_use` arrives or when `done` fires; there is no per-tool `tool_result` event.
    - `event: done\ndata: {"conversation_id": "...", "tool_calls": [...]}\n\n` — terminal success event. `tool_calls` is **byte-for-byte the same shape Day 8 returns** (`[{name, input, result}, …]`) so Day 10's ParseCard / CandidateList components consume it unchanged. This is the single rendering moment for tool results — do **not** restructure the payload in this PR.
    - `event: error\ndata: {"code": "...", "message": "..."}\n\n` — terminal failure event. The HTTP status is already 200 by the time the stream opens, so `UsageCapExceeded` / `ProviderRateLimited` / `AgentLoopLimitExceeded` cannot raise `HTTPException` the way Day 8 does — they must be caught inside the generator and yielded as an `error` frame with the same `{code, message}` shape Day 8 produces (UCAP_EXCEEDED / PROVIDER_RATE_LIMITED / LOOP_LIMIT). Frontend dispatch on `code` is unchanged from Day 10.
  - Inside the loop, on `tool_use` blocks, emit the SSE `tool_use` event, then execute the tool silently, then continue the stream. No per-tool result event.
  - Persist the assistant turn to `chat_messages` and `chat_turn_trace` **after `done`**, in the same shape Day 8 persists today. Buffer the full turn (text + tool_calls) in memory during the stream; commit both rows in one shot after the final `done` event. A mid-stream drop therefore leaves zero rows — retry re-runs the turn cleanly with no draft-row cleanup needed. (`ai_call_log` rows still write per-iteration in the existing `finally` block — audit trail stays correct even when the user-visible row never lands.)
- Frontend:
  - `frontend/src/lib/chat_stream.ts`:
    - `streamTurn({message, conversation_id, onToken, onToolUse, onDone, onError})`.
    - **Uses `fetch` with `Authorization: Bearer …` and consumes the response `ReadableStream`, parsing `event:` / `data:` / blank-line frames manually** (~20 lines). Native `EventSource` is unusable here — it cannot send custom headers in any browser, and we will not pass the JWT as a query string (referer/log leakage). There is no EventSource fallback path; `fetch` + `ReadableStream` is the only path.
    - On any non-`done` termination — HTTP error opening the stream, body parse error, network drop, or mid-stream `error` frame — invoke `onError({code, message})` with a stable shape. Don't silently swallow.
- Reconnect UX (wires into the chat UI from Day 10):
  - On `error` or unexpected disconnect, the chat UI shows: "Connection lost. [Retry]". Clicking Retry re-fires the same message with the same `conversation_id`. The backend's idempotency comes from neither `chat_messages` nor `chat_turn_trace` being written until `done` — `_load_history()` on retry sees the exact same history as the original attempt, so the turn runs cleanly. Replaces Day 10's generic "something went wrong — retry" placeholder.
- Verify Railway `drainingSeconds = 60` in `railway.json` from Day 11 (Railway's own knob, not K8s `terminationGracePeriodSeconds` — see Day 11 commit `5945465`).
- Tests:
  - `tests/test_chat_stream.py`:
    - Happy path: events arrive in order (`token`* → `tool_use`? → `token`* → `done`), terminates with `done`, both `chat_messages` and `chat_turn_trace` rows are written exactly once.
    - Error paths: `UsageCapExceeded`, `ProviderRateLimited`, and `AgentLoopLimitExceeded` each surface as an SSE `error` event carrying the right `code` — **not** an HTTP 4xx/5xx, since the response status is already 200 once the stream opens.
    - Mid-stream failure: when an exception fires after the first token but before `done`, no `chat_turn_trace` or `chat_messages` row is written. (`ai_call_log` rows for completed iterations still exist — verify that too.)
  - `tests/frontend/chat_stream.test.ts`: parses multi-line `data:` frames, dispatches the right callback per `event:` name, calls `onError` on a `done`-less termination.

## Don't

- Don't implement resumable streams (server replays cached final response). DESIGN.md §7.5 defers this until users complain. Retry re-runs the whole turn — a fresh `messages.create` per loop iteration, a fresh `ai_call_log` row per iteration, fresh tokens billed. That cost is acceptable at v1 scale; don't try to short-circuit it.
- Don't silently swallow stream errors. Surface them to the UI via the `error` event.
- Don't keep the SSE connection open after `done` — close and let the client open a new one for the next turn.
- Don't emit a per-tool `tool_result` SSE event. Tool results land in one place: `done.tool_calls`. Emitting them mid-stream creates a two-sources-of-truth problem against the `done` payload that ParseCard / CandidateList already consume.
- Don't reshape the `tool_calls` payload during this PR. Day 10's components depend on the exact Day 8 shape; transport changes, contract does not.
- Don't use native `EventSource`. It cannot carry an `Authorization` header. There is no "fallback" — `fetch` + `ReadableStream` is the only path.

## Done when

- Asking a question via the SSE endpoint yields tokens progressively (visible delay between first and last token).
- Killing the FastAPI process mid-stream produces an `error` event on the client and the Retry button works.
- A redeploy mid-stream — within the 60s grace period — completes the stream successfully.
