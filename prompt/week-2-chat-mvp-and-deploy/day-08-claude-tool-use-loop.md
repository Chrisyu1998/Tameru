# Day 8 — Minimal Claude `tool_use` agent loop with one tool

## Goal

A working `tool_use` loop in FastAPI: send a user message, Claude calls one typed tool, get a response back. No streaming yet, no UI yet — just prove the loop works end-to-end. Tomorrow adds the rest of the tools; Day 12 adds streaming.

## Read first

- `DESIGN.md` §7.1 (loop sketch — read carefully), §7.2 (typed tools, not raw SQL), §7.2.1 (5-turn history cap), §8.11 (`chat_messages` — human-visible log), §8.12 (`chat_turn_trace` — wire-shape replay log). Both schema sections are added by this day's design update.
- `CLAUDE.md` invariant 2.

## Deliverables

### Migrations (two new tables)

The chat persistence layer splits cleanly in two: one human-visible log, one wire-shape replay log. Putting both in one table forces UI reads to filter synthetic tool_result rows on every fetch and blurs two distinct concerns. See DESIGN.md §8.11/§8.12 for the full rationale.

- `supabase/migrations/<ts>_chat_messages.sql` — **human-visible log.**
  - `chat_messages(id UUID PK, user_id UUID NOT NULL FK auth.users ON DELETE CASCADE, conversation_id UUID NOT NULL, role text NOT NULL CHECK role IN ('user','assistant'), content_blocks JSONB NOT NULL, seq BIGSERIAL NOT NULL, created_at timestamptz NOT NULL DEFAULT now())`.
  - One user row + one assistant row per turn. The `assistant` row's `content_blocks` is the **final iteration's** blocks only (text). Synthetic `tool_use` / `tool_result` blocks never land here — they're in `chat_turn_trace`. Day 10's UI thread reads from this table and sees clean alternation with no filtering.
  - `conversation_id` is a plain UUID grouper, **not** an FK to a `conversations` table — v1 has no per-conversation metadata. Promote later if title/archive/share become load-bearing.
  - **`seq` is load-bearing.** The user + assistant rows are written in one batched insert, so they share `created_at` to microsecond precision; ordering by `created_at` alone returns them non-deterministically. UI reads order by `seq`.
  - Index `(user_id, conversation_id, seq)` so reads are sort-free with deterministic ordering.
  - RLS: `ENABLE` + `FORCE ROW LEVEL SECURITY`, single `FOR ALL` policy `USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())`. Audit-style INSERT-only would block a future "clear conversation" feature for no v1 benefit.

- `supabase/migrations/<ts>_chat_turn_trace.sql` — **wire-shape replay log.**
  - `chat_turn_trace(id UUID PK, user_id UUID NOT NULL FK auth.users ON DELETE CASCADE, conversation_id UUID NOT NULL, messages JSONB NOT NULL, seq BIGSERIAL NOT NULL, created_at timestamptz NOT NULL DEFAULT now())`.
  - **One row per `/chat/turn` call.** `messages` is the full Anthropic message-list slice contributed by that turn — user-typed message + every intermediate `(assistant_with_tool_use, user_with_tool_result)` pair + final assistant blocks. Replay concatenates the `messages` arrays from the last 5 trace rows.
  - **The cap maps exactly.** "Last 5 turns" (DESIGN.md §7.2.1) = `LIMIT 5` on this table regardless of hop count.
  - Why faithful replay matters: a follow-up turn that references prior tool output ("what about coffee?" after a Dining total) only grounds correctly when Claude sees the prior `tool_use` + `tool_result` pair. Storing only the prose loses that context.
  - Index `(user_id, conversation_id, seq DESC)` for the descending-LIMIT-5 read.
  - RLS: same `FOR ALL` shape as `chat_messages`.

- `ai_call_log.task_type` already includes `chat_turn` (Day 4 migration) — no schema change there.

### `app/prompts/chat.py`

- `PROMPT_VERSION = "chat_v1"` — bumped on substantive edits to either `SYSTEM_PROMPT` or the tool-schema set, so eval harnesses can detect regressions.
- `SYSTEM_PROMPT` — minimal stub for Day 8: instruct Claude to use `calculate_total` for spending totals, otherwise answer in prose. Day 9 expands this when the rest of the tool surface lands; Day 16 adds the user-memory block. Keep it short — the substantive prompt comes later, and rewriting it twice is the alternative.
- `def render_system_prompt() -> str` — for Day 8, just returns `SYSTEM_PROMPT`. The function exists today so Day 9/16 can extend it (merchant block, memory block) without changing the call site in the loop.
- `def system_prompt_hash(rendered: str, tool_schemas: list[dict]) -> str` — SHA-256 of `rendered` + canonical JSON of `tool_schemas`. This becomes `ai_call_log.prompt_hash`. **Hash the system prompt + tool schemas, not the user message** — privacy posture (CLAUDE.md): hashes of user content don't belong in the audit log even though they're not reversible.

### `app/agent/tools.py`

- One tool today: `calculate_total({category?, card_id?, date_from?, date_to?}) -> {total, count}`.
- Implementation queries `transactions` via `supabase_for_user(user_jwt)` — RLS scopes the query.
- **Aggregation approach:** PostgREST has no clean SUM via the Python SDK. Fetch matching rows (using the same filter shape as `app/services/transactions.py::list_transactions`) and sum amounts in Python. Hard-cap at 5,000 rows; if the cap is hit, return `{"truncated": true, ...}` so Claude can surface the limit. Decimal arithmetic — never float. Day 9 may extract a shared filter builder once `get_transactions` lands; not today.
- Tool schema in Anthropic's `tools` array format. Export `CALCULATE_TOTAL_TOOL` and a `TOOL_REGISTRY: dict[str, Callable]` mapping name → executor. The registry shape is what Day 9 extends.

### `app/agent/loop.py`

- `def run_turn(user: AuthedUser, conversation_history: list[dict], user_message: str) -> AssistantTurn`:
  - **Sync, not async.** The codebase is fully sync; FastAPI runs sync handlers in a threadpool. Going async here would require either `AsyncAnthropic` paired with sync Supabase calls (which would block the event loop unless wrapped in `run_in_threadpool`) or a porting of `app/db.py` to expose an async client. Neither earns its keep at v1 scale (~10 invite-only users); revisit when threadpool saturation is a measured problem, not a hypothetical one.
  - Calls `anthropic.Anthropic().messages.create()` (non-streaming today) with `tools=[CALCULATE_TOTAL_TOOL]`, `system=render_system_prompt()`, and `messages=conversation_history + [{"role": "user", "content": user_message}]`.
  - Loops: if `response.stop_reason == "tool_use"`, execute each `tool_use` block via `TOOL_REGISTRY[name]`, append the assistant's content blocks and the corresponding `tool_result` blocks to messages, call again. Cap at 8 iterations as a safety stop.
  - **Unknown tool name:** return a `tool_result` block with `is_error: true` and a short text body. Don't crash the loop — Claude can recover or surface the error.
  - **8-iteration cap fires:** raise `AgentLoopLimitExceeded`. The route handler turns that into a 5xx with a generic user-facing message; the partial assistant text isn't persisted.
  - Returns `AssistantTurn(assistant_text, content_blocks, turn_messages, tool_calls)` where `ToolCallRecord = {name, input, result}`. Three artifacts, three consumers: `assistant_text` → chat bubble; `content_blocks` → `chat_messages.content_blocks` (final-iteration only, human-visible); `turn_messages` → `chat_turn_trace.messages` (the FULL Anthropic message-list slice for this turn, including intermediate tool_use / tool_result pairs, persisted so the next turn can replay them faithfully). `tool_calls` is iterated by Day 10's UI to render ParseCard / CandidateList components.
  - Logs every model call (one per loop iteration) to `ai_call_log` via Day 4's `log_ai_call`, with `task_type="chat_turn"`, `prompt_version=PROMPT_VERSION`, `prompt_hash=system_prompt_hash(...)`. Use the user-JWT path (CLAUDE.md invariant 14) — never `supabase_admin`.

### `app/routes/chat.py`

- `POST /chat/turn` — body: `{conversation_id?: UUID, message: str}`. If `conversation_id` is omitted, mint one server-side and return it.
- Auth: `Depends(get_current_user_with_device)` — same gate the rest of the authed routes use.
- **History load:** select last 5 rows from `chat_turn_trace` for this `conversation_id` ordered by `seq DESC LIMIT 5`, reverse to chronological order, concatenate the `messages` arrays. Result is the full Anthropic-shaped message list including prior tool_use / tool_result pairs. The 5-turn cap is from DESIGN.md §7.2.1; encoding it from day one means Day 16's memory layer doesn't retrofit it. (Older turns will be summarized into `user_memory` by Day 16; Day 8 just truncates.)
- **Persist** to both tables. Trace first (load-bearing for replay): one `chat_turn_trace` row with `messages=turn.turn_messages`. Then human-visible: one `chat_messages` row with `role='user'`, `content_blocks=[{"type":"text","text":message}]`, plus one with `role='assistant'`, `content_blocks=turn.content_blocks`. Two-table atomicity: Supabase Python has no transaction primitive across tables, so a partial write is technically possible — accept this for v1 (worst case is a brief UI/replay desync that resolves on the next turn). Stronger atomicity is a Day 12+ concern.
- Returns `{conversation_id, assistant_text, tool_calls: [{name, input, result}]}`. Day 10's chat UI consumes this shape; Day 12 swaps the wire to SSE while keeping the same `tool_calls` semantics in the `done` event.
- `AgentLoopLimitExceeded` → 500 with `{"code": "LOOP_LIMIT", "message": "..."}`.

### Tests

- `tests/test_agent_loop.py` — mock Anthropic responses to test:
  - One-hop turn (no tools) returns text.
  - Two-hop turn (one tool call + final synthesis) executes the tool against a seeded `transactions` row and returns prose containing the right number.
  - Eight-hop loop limit is enforced.
  - Each iteration writes one `ai_call_log` row with `task_type='chat_turn'`, `success=True`, and `prompt_version=PROMPT_VERSION`.
  - Unknown tool name produces an `is_error` `tool_result` block, not an exception.

### DESIGN.md

- Add `§8.11 chat_messages` (human-visible log) and `§8.12 chat_turn_trace` (wire-shape replay log) describing the schemas and RLS shape above, plus the rationale for the split. Update `§7.2.1` to reference `chat_turn_trace` for the cap source-of-truth. Keep `§7.6` ("stateless from the app's perspective") consistent — clarify that DB persistence makes the conversation survive page reload; the loop itself is still stateless across calls.

## Don't

- Don't add streaming — Day 12 owns SSE.
- Don't add the other 6 tools today — Day 9 owns them.
- Don't use Claude's Managed Agents or the standalone Agent SDK. Messages API + `tool_use` blocks via the `anthropic` SDK directly. (See `CLAUDE.md` invariant 2 for why.)
- Don't make the loop async. The codebase is sync; FastAPI's threadpool is the concurrency mechanism. Async here without porting Supabase + Anthropic + every tool to async is worse, not better.
- Don't hash or log the user's message text into `ai_call_log.prompt_hash`. Hash the system prompt + tool schemas only (privacy posture).
- Don't write `ai_call_log` via `supabase_admin` — user-JWT + narrow INSERT policy (invariant 14).
- Don't INSERT into `chat_messages` or `chat_turn_trace` from the tool implementation. The route handler is the only writer to either.
- Don't store synthetic `tool_use` / `tool_result` blocks in `chat_messages`. Those belong in `chat_turn_trace` only — the human-visible log stays clean.
- Don't read `chat_messages` to reconstruct history for the loop. The loop reads `chat_turn_trace` because faithful replay needs the full block sequence (DESIGN.md §8.12).

## Done when

- `curl -X POST /chat/turn -d '{"message": "How much did I spend on Dining last month?"}'` with a real JWT returns prose with the right number, executed via `calculate_total`.
- `ai_call_log` shows the right number of rows for the turn (one per model call) with `task_type='chat_turn'`, `prompt_version='chat_v1'`.
- `chat_messages` shows two rows per turn (one `user`, one `assistant`) under the same `conversation_id` regardless of how many tool hops the turn took. `chat_turn_trace` shows one row per turn whose `messages` array contains the full hop sequence.
- A follow-up turn that references prior tool output (e.g. "what about coffee?" after a Dining total) reaches Claude with the prior `tool_use` + `tool_result` blocks intact in the replayed history.
- The 8-hop safety cap fires when given a deliberately confused prompt, and the partial assistant turn is **not** persisted.
- `tests/test_agent_loop.py` passes.
