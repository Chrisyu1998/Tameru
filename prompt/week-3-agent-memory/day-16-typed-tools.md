# Day 16 — All 7 typed tools + middleware (logging, usage cap, 429 backoff)

## Goal

Implement the remaining 6 typed tools from `DESIGN.md` §7.2. Add middleware around tool execution: AICallLog write per call, per-user usage cap check, 429 retry with backoff.

## Read first

- `DESIGN.md` §7.1 (middleware section), §7.2 (full tool list), §7.3 (concurrency + 429 handling).

## Deliverables

- `app/agent/tools.py` — implement all 7:
  - `get_transactions({category?, card_id?, date_from?, date_to?, limit?})` → `Transaction[]`. **Hard cap: `limit` defaults to 50, max 500. The function clamps any larger value silently and includes `truncated: true` in the response when it does.**
  - `calculate_total({...})` → `{total, count}` (already done Day 15) — single number, no cap needed.
  - `get_subscriptions({status?})` → `Subscription[]`. Hard cap: 200 rows (no user has more in practice).
  - `add_transaction({merchant, amount, date, card_id, category, notes?})` → `Transaction`. Single insert, no cap needed.
  - `get_spending_summary({months?})` → `CategoryBreakdown[]`. Bounded by category count (~20 max).
  - `get_cards()` → `Card[]`. Bounded by user's card count (typically <10).
  - `set_goal({category?, amount, period})` → `Goal`. Single insert.

**Why hard caps matter:** Haiku's 200K context is plenty for normal turns (~5K per turn — see DESIGN.md §7.2.1) but a pathological tool call returning 10K transaction rows would blow the budget. Caps are enforced inside the tool function, not relied on from the model. The agent sees `truncated: true` and can either narrow the query or surface the limitation in its prose answer.
- New migration: `goals(id, user_id, category, amount, period, created_at)` with RLS.
- `app/agent/middleware.py`:
  - `log_tool_call(user_jwt, tool_use_block)` — writes a row to `ai_call_log` before tool execution. Captures the model's reasoning by hashing the prompt that produced the call.
  - `assert_within_usage_cap(user_jwt)` — sums today's `chat_turn` input+output tokens for this user; raises `UsageCapExceeded` if over the configured limit. Initial cap: 200K tokens/day per user (configurable via `CHAT_USAGE_CAP_TOKENS_PER_DAY` env var). At ~19K tokens per turn that's ~10 turns/day — plenty for real use, blocks runaway spam. See DESIGN.md §11.2 for the cost-ceiling rationale.
  - When the cap is exceeded, surface a stable error code `{"code": "DAILY_CAP_EXCEEDED", "message": "You've used your daily AI quota — resets at midnight UTC."}` so the frontend can render friendly copy (Day 18 owns that UI).
  - `with_429_backoff(coro)` decorator — catches Anthropic 429s, sleeps 2s, retries once, then surfaces a `{user_facing: "Rate limit hit, try again in a moment"}` error.
- Wire all three into `app/agent/loop.py` between iterations.
- Tests:
  - `tests/test_tools.py` — for each tool, an integration test that invokes it through the loop with a mocked Claude that calls only that tool. Assert the right Supabase rows are created/read.
  - `tests/test_usage_cap.py` — seed `ai_call_log` with high token counts; assert the next turn raises `UsageCapExceeded`.

## Don't

- Don't expose a `run_query(sql)` tool. Phase 2.
- Don't bypass `supabase_for_user(user_jwt)` in any tool — RLS is the safety net.
- Don't make the usage cap a soft warning — hard fail. The user can see usage in Settings later.

## Done when

- `pytest tests/test_tools.py` passes for all 7 tools.
- A turn that asks "What are my top categories this month?" calls `get_spending_summary` and returns the right list.
- A user past their token cap gets the rate-limit message instead of a turn.
