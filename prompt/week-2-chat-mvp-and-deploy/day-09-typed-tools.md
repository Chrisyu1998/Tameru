# Day 9 — All typed tools (reads + propose-then-confirm writes) + middleware

## Goal

Implement the full tool surface from `DESIGN.md` §7.2. Reads return data. Writes follow the **propose-then-confirm** pattern (CLAUDE.md invariant 8): the tool returns a proposal payload the React client renders as a preview card, and a separate confirm endpoint writes the row only after the user taps "looks right." Add middleware around tool execution: `ai_call_log` write per call (user-JWT path — invariant 14), per-user daily usage cap check, 429 retry with backoff.

## Read first

- `DESIGN.md` §7.1 (middleware section), §7.2 (full tool list + propose-then-confirm rationale), §7.3 (concurrency + 429 handling), §6.2 (chat-based write flow).
- `CLAUDE.md` invariants 1, 2, 8, 14.

## Deliverables

### `app/agent/tools.py` — reads

These return data directly, no user confirmation step.

- `get_transactions({category?, card_id?, merchant_contains?, date_from?, date_to?, amount_min?, amount_max?, limit?})` → `Transaction[]`. **Hard cap:** `limit` defaults to 50, max 500. Clamps silently and includes `truncated: true` when it does. The ambiguity parameters (`merchant_contains`, `amount_min/max`) power chat-based disambiguation — when the user says "change that $10 coffee from last week" and there are multiple matches, the agent calls `get_transactions` with narrow filters and the React chat UI renders the result as tappable candidate cards (UX frame 11b flow).
- `calculate_total({...})` → `{total, count}` (already done Day 8) — single number, no cap needed.
- `get_subscriptions({status?})` → `Subscription[]`. Hard cap: 200 rows.
- `get_spending_summary({months?})` → `CategoryBreakdown[]`. Bounded by category count (~20 max).
- `get_cards()` → `Card[]`. Bounded by user's card count (typically <10).

**Why hard caps matter:** Haiku's 200K context is plenty for normal turns (~5K per turn — see DESIGN.md §7.2.1) but a pathological tool call returning 10K transaction rows would blow the budget. Caps are enforced inside the tool function, not relied on from the model.

### `app/agent/tools.py` — writes (propose-then-confirm)

**No write happens inside the tool.** Each of these returns a proposal payload; the React client renders the preview card; a separate `POST /<resource>/confirm` endpoint (Day 5 for transactions, Day 14 for cards, Day 19 for subscriptions) writes the row after the user taps "looks right."

- `propose_transaction({merchant, amount, date, card_id?, category?, notes?})` → `TransactionProposal`. Tool impl normalizes the merchant (Day 4 `normalize_merchant`), calls `categorize()` (Day 4) to fill `category` if not provided, resolves `card_id` from a card name if Claude passed one, and returns the structured proposal. **Does not INSERT.**
- `propose_card({network, last4, program, alias?})` → `CardProposal`. Tool impl calls Perplexity (Day 14 `lookup_card`) to populate multipliers and source_urls, returns the proposal. **Does not INSERT.** **Stub note:** Day 14 (`lookup_card`) lands after this day in the reordered plan. Ship `propose_card` today as a stub that returns `{error: "card_lookup_unavailable", message: "Card add will work once Day 14 ships."}` wrapped in the tool response. Claude will surface that text to the user. Day 14 replaces the stub with the real Perplexity-backed impl in a one-line change.
- `propose_subscription({name, amount, frequency, start_date, category?, card_id?})` → `SubscriptionProposal`. Tool impl computes `next_billing_date` from `start_date + frequency`, returns the proposal. **Does not INSERT.** **Stub note:** Day 19 ships the `POST /subscriptions/confirm` endpoint. `propose_subscription` can ship fully today (it only computes a payload), but the end-to-end "looks right" tap will fail until Day 19 lands. That's fine — either stub the same way as `propose_card` above, or leave the tool live and accept a 404 on confirm until Day 19.
- `set_goal({category?, amount, period})` → `Goal`. **Direct write** — goals are low-risk, reversible, and not on the transaction ledger. The propose-confirm ceremony is not worth it here.

**Why propose-then-confirm for the ledger writes?** Transactions, cards, and subscriptions show up on the user's ledger; a row written from a misread message erodes trust. The proposal pattern makes the UI the point of commit: no row exists until the user taps a button. Matches the Intent Preview pattern from 2026 agentic-UX design literature.

### `app/agent/tools.py` — edits and deletes are not chat tools

Edits and deletes happen in the edit sheet (UX frame 11b), reached from the transaction list (UX frame 11a) or from an in-chat candidate list produced by `get_transactions(...)`. The agent does **not** have `edit_transaction` or `delete_transaction` tools. Its role in mutations is limited to `propose_*` creates and `get_transactions(...)` retrieval for disambiguation. Claude cannot silently edit or delete rows via tool_use.

### New migration

- `goals(id, user_id, category, amount, period, created_at)` with RLS.

### `app/agent/middleware.py`

- `log_tool_call(user_jwt, tool_use_block)` — writes a row to `ai_call_log` before tool execution, via Day 4's `log_ai_call` helper. **Uses the user JWT path** (`supabase_for_user` + narrow INSERT policy — invariant 14). Captures the model's reasoning by hashing the prompt that produced the call.
- `assert_within_usage_cap(user_jwt)` — sums today's `chat_turn` input+output tokens for this user; raises `UsageCapExceeded` if over the configured limit. Initial cap: 200K tokens/day per user (`CHAT_USAGE_CAP_TOKENS_PER_DAY`). See DESIGN.md §11.2.
- On cap exceeded: surface `{"code": "DAILY_CAP_EXCEEDED", "message": "You've used your daily AI quota — resets at midnight UTC."}` for Day 10's UI.
- `with_429_backoff(coro)` — catches Anthropic 429s, sleeps 2s, retries once, then surfaces a `{user_facing: "Rate limit hit, try again in a moment"}` error.

Wire all three into `app/agent/loop.py` between iterations.

### System-prompt blocks — merchants + memory

The agent's system prompt is assembled from static preamble + several user-specific blocks, concatenated once per turn. Day 16 adds `render_user_memory()` (cross-session facts). This day adds `render_user_merchants()` for **merchant canonicalization** on the chat-typed path.

- `def render_user_merchants(user_jwt) -> str` — returns a system-prompt block listing this user's top 30 merchants by combined recency + frequency score, pulled from the user's `transactions` table via `supabase_for_user(user_jwt)`. Budget: ~300 tokens (each merchant plus a count is ~8 tokens; 30 × 8 ≈ 240 tokens plus a framing sentence). The block instructs Claude: *"When the user mentions a merchant that closely matches one of these, prefer the exact spelling here for `propose_transaction(merchant=...)`. This is how we avoid fragmenting the user's history across spelling variants."*
- **Why this matters:** chat-based transaction entry is our only create surface (invariant 8). A user typing "spent $10 at KFC" in chat, or speaking the same via Web Speech API (UX frame 14), enters through the Claude agent loop. Claude is the point of write-side canonicalization — if it sees "Kentucky Fried Chicken" in the user's history, it fills `propose_transaction(merchant="Kentucky Fried Chicken", ...)` instead of creating a new `kfc` row. This is the v1 solution to merchant fragmentation; DESIGN.md §3.4 documents why the alternatives (autocomplete on a free-form chat input, or nightly Gemini merge jobs) don't fit v1.
- **What it does NOT handle:** first-time merchants (no history to normalize against — accept it), CSV imports (not on the chat path — Phase 2 nightly merge job is the eventual fix), and the edit sheet (Day 15 — users who retype a merchant there can fragment; accept for v1).
- Cache shape: cheap. Call once per turn start, not per tool iteration. The list changes slowly enough that aggressive caching (per-request memoization inside the loop) is fine; no need for cross-request cache at v1 scale.
- Tests: seed 5 transactions with merchant `"Kentucky Fried Chicken"` for user A. Call `render_user_merchants(user_a.jwt)` and assert the returned block contains that exact string. Run a turn for "spent $10 at KFC" against a mocked Claude that echoes its system prompt back; assert the merchant block is present.

### Tests

- `tests/test_tools.py` — for each read tool and each `propose_*` tool, an integration test that invokes it through the loop with a mocked Claude. For `propose_*`, assert that **no row is written to the underlying table** — only the proposal payload is returned.
- `tests/test_tools.py` — disambiguation path: seed 3 "coffee" transactions, call `get_transactions(merchant_contains="coffee")` through the loop, assert 3 rows returned in candidate-card shape.
- `tests/test_usage_cap.py` — seed `ai_call_log` with high token counts; assert the next turn raises `UsageCapExceeded`.

## Don't

- Don't give Claude a direct-write `add_transaction`, `add_card`, or `add_subscription` tool. All ledger creates go through propose → UI confirm → server write.
- Don't give Claude `edit_transaction` or `delete_transaction` tools. Mutations of existing rows happen in the edit sheet, reached from a tap.
- Don't expose a `run_query(sql)` tool. Phase 2.
- Don't bypass `supabase_for_user(user_jwt)` in any tool — RLS is the safety net.
- Don't use `supabase_admin` to write `ai_call_log` rows from the middleware. User-JWT + narrow INSERT policy only (invariant 14).
- Don't make the usage cap a soft warning — hard fail. Users see usage in Settings later.

## Done when

- `pytest tests/test_tools.py` passes for all reads and `propose_*` tools.
- A turn that asks "How much on dining this month?" calls `get_spending_summary` or `calculate_total` and returns the right number.
- A turn that says "spent $47 at Trader Joe's on my Amex Gold" calls `propose_transaction` and returns a `TransactionProposal` payload — **and no row exists in `transactions` until `POST /transactions/confirm` is separately called.**
- A turn that says "change that $10 coffee from last week" (with multiple matches seeded) calls `get_transactions(merchant_contains="coffee", amount_min=9, amount_max=11, date_from=...)` and returns multiple rows for UI disambiguation.
- A user past their token cap gets the `DAILY_CAP_EXCEEDED` error instead of a turn.
