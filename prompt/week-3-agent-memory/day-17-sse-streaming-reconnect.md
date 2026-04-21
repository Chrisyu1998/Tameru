# Day 17 — SSE streaming + frontend reconnect

## Goal

Convert `POST /chat/turn` to stream tokens via Server-Sent Events. Frontend renders progressively. On stream drop, show a reconnect button.

## Read first

- `DESIGN.md` §7.1 (loop sketch with `yield sse_event`), §7.5 (streaming + reconnect failure mode).

## Deliverables

- Backend:
  - `POST /chat/turn` → returns `Content-Type: text/event-stream`. The response body yields events:
    - `event: token\ndata: <text chunk>\n\n` for each text token from Claude.
    - `event: tool_use\ndata: {"name": "...", "input": {...}}\n\n` for each tool call.
    - `event: tool_result\ndata: {"name": "...", "result_summary": "..."}\n\n` for each tool result (truncated for UI display).
    - `event: done\ndata: {"conversation_id": "...", "assistant_turn_id": "..."}\n\n`.
    - `event: error\ndata: {"code": "...", "message": "..."}\n\n` on failure.
  - Use `anthropic.messages.stream()`. Inside the loop, on `tool_use` blocks, emit the SSE event then execute and emit `tool_result`, then continue the stream.
  - Persist the assistant turn to `chat_messages` after `done`.
- Frontend:
  - `frontend/src/lib/chat_stream.ts`:
    - `streamTurn({message, conversation_id, onToken, onToolUse, onToolResult, onDone, onError})`.
    - Uses native `EventSource` (with `Authorization` header — falls back to `fetch` + `ReadableStream` if `EventSource` headers aren't supported).
    - On `error`, surfaces the failure to the caller with a stable shape.
- Reconnect UX (built today, used in Day 18):
  - On `error` or unexpected disconnect, the chat UI shows: "Connection lost. [Retry]". Clicking Retry re-fires the same message with the same `conversation_id`. The backend's idempotency comes from the assistant turn not being persisted until `done`.
- Verify Railway grace period from Day 7 is set to 60s.
- Tests:
  - `tests/test_chat_stream.py`: integration test that connects to the SSE endpoint, asserts events arrive in order, ends with `done`.

## Don't

- Don't implement resumable streams (server replays cached final response). Design defers this.
- Don't silently swallow stream errors. Surface them to the UI.
- Don't keep the SSE connection open after `done` — close and let the client open a new one for the next turn.

## Done when

- Asking a question via the SSE endpoint yields tokens progressively (visible delay between first and last token).
- Killing the FastAPI process mid-stream produces an `error` event on the client and the Retry button works.
- A redeploy mid-stream — within the 60s grace period — completes the stream successfully.
