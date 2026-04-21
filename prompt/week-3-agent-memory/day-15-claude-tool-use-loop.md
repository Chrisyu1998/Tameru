# Day 15 — Minimal Claude `tool_use` agent loop with one tool

## Goal

A working `tool_use` loop in FastAPI: send a user message, Claude calls one typed tool, get a response back. No streaming yet, no UI yet — just prove the loop works end-to-end. Tomorrow adds the rest of the tools; Day 17 adds streaming.

## Read first

- `DESIGN.md` §7.1 (loop sketch — read carefully), §7.2 (typed tools, not raw SQL).
- `CLAUDE.md` invariant 2.

## Deliverables

- `app/agent/tools.py`:
  - Define one tool today: `calculate_total({category?, card_id?, date_from?, date_to?}) -> {total, count}`. Implementation queries `transactions` via `supabase_for_user(user_jwt)` — RLS scopes the query.
  - Tool schema in Anthropic's `tools` array format.
- `app/agent/loop.py`:
  - `async def run_turn(user_jwt, conversation_history, user_message) -> AssistantTurn`:
    - Calls `anthropic.messages.create()` (non-streaming today) with `tools=[CALCULATE_TOTAL_TOOL]`.
    - Loops: if response contains `tool_use` blocks, execute each via the typed function, append `tool_result` blocks, call again. Cap at 8 iterations as a safety stop.
    - Returns the final assistant text + the full block sequence (for AICallLog).
  - Logs every model call (one per loop iteration) to `ai_call_log` with `task_type="chat_turn"`, including tool_use block details in a structured field if you want.
- `app/routes/chat.py`:
  - `POST /chat/turn` — body: `{conversation_id?, message}`. Loads prior history from `chat_messages` (new table), runs the turn, persists assistant turn, returns `{assistant_text, tool_calls: [...]}`.
- New migration: `chat_messages(id, user_id, conversation_id, role, content_blocks JSONB, created_at)` with RLS.
- `tests/test_agent_loop.py`:
  - Mock Anthropic responses to test:
    - One-hop turn (no tools) returns text.
    - Two-hop turn (one tool call + final synthesis) executes the tool and returns prose.
    - Eight-hop loop limit is enforced.

## Don't

- Don't add streaming — Day 17 owns SSE.
- Don't add the other 6 tools today — Day 16 owns them.
- Don't use Claude's Managed Agents or the standalone Agent SDK. Messages API + `tool_use` blocks via the `anthropic` SDK directly. (See `CLAUDE.md` invariant 2 for why.)
- Don't make the loop synchronous-blocking — use `async`.

## Done when

- `curl -X POST /chat/turn -d '{"message": "How much did I spend on Dining last month?"}'` with a real JWT returns prose with the right number, executed via `calculate_total`.
- `ai_call_log` shows the right number of rows for the turn (one per model call).
- The 8-hop safety cap fires when given a deliberately confused prompt.
