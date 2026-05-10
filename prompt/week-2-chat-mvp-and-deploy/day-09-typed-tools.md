# Day 9a — Read tools + middleware (cap, 429 backoff, system prompt rewrite)

## Scope split

Day 9 was originally a single day covering read tools, propose tools, `set_goal`, merchant canonicalization, and three middleware components. That bundle was too large — a regression in any one piece would force reverting all of them — and contained at least three latent bugs (cap-check timing, double audit logging, prompt-cache invalidation by per-user system blocks). Day 9 is now split:

- **9a (this prompt):** read tools, middleware (usage cap + 429 backoff), and a system-prompt rewrite that documents the new tool surface.
- **9b (separate prompt):** `propose_transaction`, `set_goal` with a safe-upsert `goals` migration, and the invariant-guard test that protects "only `set_goal` writes from inside `tool_use`."
- **9c (separate prompt):** `render_user_merchants` and the two-block system-prompt assembly with an Anthropic prompt-cache breakpoint between the static preamble and the per-user merchants block.

`propose_card` and `propose_subscription` are **not** part of any 9x sub-day. Their schemas and registry entries land on **Day 14** and **Day 19** respectively, alongside the Perplexity lookup and the `pg_cron` auto-logger that make those tools end-to-end usable. Registering a tool whose confirm endpoint or upstream dependency does not yet exist would mean Claude calls a tool that returns a user-visible error — worse UX than not having the tool. See "Don't" below.

## Goal

Implement the read half of the typed tool surface from `DESIGN.md` §7.2. Reads return data; no `propose_*` tools today. Wire two pieces of middleware around the Claude call: a per-user daily token cap (checked at turn entry, lenient mid-turn) and a 429 retry-once-then-fail wrapper. Rewrite the system prompt to disambiguate the now-overlapping tools and bump `PROMPT_VERSION` so audit rows segregate cleanly across the change.

## Read first

- `DESIGN.md` §7.1 (loop sketch + middleware), §7.2 (typed-tool list and rationale), §7.2.1 (context-window math), §7.3 (concurrency + 429 handling), §11.2 (daily cap), §14.1 (90-day rollup rule).
- `CLAUDE.md` invariants 1, 2, 14.
- Day 8's existing loop at `app/agent/loop.py` — read carefully; the per-`messages.create()` `ai_call_log` row already covers what we need for the cap. Do not add a second per-tool log; see "Don't."

## Deliverables

### Read tools — `app/agent/tools.py`

All read tools return data directly. Each one delegates to a service-layer function or uses the user's JWT (`supabase_for_user(user.jwt)`) so RLS is the safety net. Hard caps are enforced inside the tool, not relied on from the model.

- `get_transactions(filters: TransactionFilters) → {items: TransactionRow[], has_more: bool}`. **Implementation:** delegate to `list_transactions` in `app/services/transactions.py` (Day 5). Do not re-implement the query builder; one query surface, two callers (HTTP + agent). The tool's schema mirrors `TransactionFilters` exactly (`card_id`, `category`, `merchant_contains`, `date_from`, `date_to`, `amount_min`, `amount_max`, `limit`, `offset`). `limit` defaults to 50, clamped to `MAX_LIMIT=500` silently with `has_more=true` when more rows exist. The ambiguity parameters (`merchant_contains`, `amount_min/max`) are what power chat-based disambiguation: when the user says "change that $10 coffee from last week," the agent calls `get_transactions` with narrow filters and the Day 10 chat UI renders the rows as tappable candidate cards (§6.2).

- `calculate_total(filters: TransactionFilters) → {total, count, truncated}` — **already exists from Day 8 but must be widened.** Today it accepts only `{category, card_id, date_from, date_to}`. After this day it accepts the full `TransactionFilters` shape so "how much did I spend at Trader Joe's this month?" routes correctly to this tool instead of forcing Claude into `get_transactions` + in-head summation. Update the schema, the executor signature, and the existing tests.

- `get_subscriptions({status?}) → Subscription[]`. Hard cap: 200 rows.
- `get_spending_summary({months?: int = 1}) → CategoryBreakdown[]`. `months` is "last N calendar months including the current one"; default 1 (this month only). Bounded by category count (~20 max). Spec it deterministically — pick the rolling-window definition now so future drift is impossible.
- `get_cards() → Card[]`. Bounded by the user's card count (typically <10).

**Shared filter builder — required, not optional.** Extract `apply_transaction_filters(query, filters: TransactionFilters)` in `app/services/transactions.py` and route both `list_transactions` (the existing service function) and `calculate_total` (in `app/agent/tools.py`) through it. Today's `calculate_total` has a divergent filter implementation; consolidating it now prevents drift between "filters available on the HTTP list endpoint" and "filters available to the agent."

**Why hard caps matter:** Haiku 4.5's 200K context is ample for normal turns (~5K per turn — §7.2.1) but a pathological `get_transactions(limit=10000)` would blow the budget. Caps live inside the tool function.

### Middleware — `app/agent/middleware.py`

The loop is sync (Day 8's decision; see its prompt for rationale). Middleware below is plain functions.

#### `assert_within_usage_cap(user)` — fires once at turn entry

Checked **at turn entry only**, before the first `messages.create()`. If the user is already at or above their daily cap, raise `UsageCapExceeded` and the route handler returns the structured error described below. If they pass the entry check, the turn runs to completion even if intermediate iterations push them over the cap — aborting mid-turn produces partial responses for at most one turn (~19K tokens, ~$0.018) of overshoot, which is a better trade than confusing UX.

**Query shape** (raw `ai_call_log`, *not* the rollup):

```sql
SELECT COALESCE(SUM(input_tokens + output_tokens), 0)
FROM ai_call_log
WHERE user_id = auth.uid()
  AND task_type = 'chat_turn'
  AND timestamp >= date_trunc('day', now() AT TIME ZONE 'UTC');
```

- **User JWT path only** — `supabase_for_user(user.jwt)`. RLS scopes the read. Never reach for the service role (invariant 1, invariant 14).
- **UTC midnight** explicitly. Matches user-facing copy ("resets at midnight UTC"). Don't rely on `CURRENT_DATE`, which is server-timezone-sensitive.
- **`task_type='chat_turn'` filter** — Gemini categorization tokens (`task_type='categorization'`) are written by the propose-transaction path but do not count against the chat cap.
- **Don't query `ai_call_log_daily`** — that's the >90-day archive (§14.1). Today's data is never in the rollup. Add a one-line comment in the implementation explaining this so a future "optimization" doesn't break the cap silently.

**Cap value:** read from `os.environ.get("CHAT_USAGE_CAP_TOKENS_PER_DAY")`, default `200_000` (DESIGN.md §11.2).

**Error shape** surfaced to the route handler and to Day 10's UI:

```python
{"code": "DAILY_CAP_EXCEEDED",
 "message": "You've used your daily AI quota — resets at midnight UTC."}
```

#### `with_429_backoff(call: Callable[[], T]) -> T`

Wraps the `messages.create()` call. Catches **only** `anthropic.RateLimitError`. One retry after `time.sleep(2)`. On the second failure, raise `AgentLoopError("AI_PROVIDER_RATE_LIMITED")`. Other exceptions (`anthropic.BadRequestError`, network errors, anything non-429) must propagate unchanged — swallowing them hides real bugs.

```python
def with_429_backoff(call: Callable[[], T]) -> T:
    try:
        return call()
    except anthropic.RateLimitError:
        time.sleep(2)
        try:
            return call()
        except anthropic.RateLimitError as exc:
            raise AgentLoopError("AI_PROVIDER_RATE_LIMITED") from exc
```

Wire it around the `client.messages.create(...)` call at `app/agent/loop.py:198`. The existing `ai_call_log` write at `app/agent/loop.py:230` still fires on success and on caught-and-re-raised exceptions (the bare `except Exception` branch at `app/agent/loop.py:210`), so the audit trail covers both attempts.

#### No `log_tool_call` middleware

Originally proposed in the unified Day 9 prompt. Dropped because:
- Day 8 already writes one `ai_call_log` row per `messages.create()` call (`task_type='chat_turn'`) at `app/agent/loop.py:230`. That row carries the only thing the cap actually needs — token counts from `response.usage`.
- A per-`tool_use` row would either double-count tokens (the tool didn't make an LLM call) or write meaningless `0/0` rows.
- Worse, `task_type='tool_use'` would violate the CHECK constraint at `supabase/migrations/20260421120800_ai_call_log.sql:35-39` and every tool call would fail.
- The per-turn tool trace the UI needs is already in `AssistantTurn.tool_calls` (`app/agent/loop.py:109`) and persisted via `chat_turn_trace.messages` — `ai_call_log` would be a third copy in the wrong table.

### System prompt rewrite — `app/prompts/chat.py`

The prompt today (`SYSTEM_PROMPT` at `app/prompts/chat.py:29`) mentions only `calculate_total`. After this day Claude has five tools (`calculate_total`, `get_transactions`, `get_subscriptions`, `get_spending_summary`, `get_cards`) — several of which overlap in plausible-use space. Without disambiguation prose, Haiku will sometimes pull rows via `get_transactions` and sum in its head, producing slow + occasionally wrong totals.

**Rewrite goals:**

- Describe each tool in one sentence and pin **when to pick which**. Concretely call out: *aggregate / total / "how much" → `calculate_total`; list / find / "which ones" / disambiguation → `get_transactions`.*
- Document the `truncated: true` and `has_more: true` flags and instruct Claude to surface them.
- Preserve the existing "ask one short clarifying question instead of guessing" guidance.
- **No mention of propose_* tools yet** — those land in 9b and Claude shouldn't be told about a tool surface that isn't registered.

**Bump `PROMPT_VERSION` to `"chat_v2"`** at `app/prompts/chat.py:26`. This is mechanical but load-bearing: every `ai_call_log` row carries `prompt_version`, and grouping cost/regression queries by prompt version is the only way to detect "did rewriting the prompt change Haiku's tool-selection rate" once you have a few days of data on each side.

`render_system_prompt()` keeps its zero-argument signature today. The `user_jwt` parameter and the two-block cache-aware structure both land in 9c.

### Tests

- `tests/test_tools.py` — for each new read tool, an integration test that invokes it through `execute_tool()` with a fake `AuthedUser` against a seeded supabase fixture (or mocked client, matching Day 8's test style). Assert filter shapes match `TransactionFilters`, hard caps clamp silently, and `truncated`/`has_more` flags fire correctly.
- `tests/test_tools.py` — disambiguation path: seed 3 "coffee" transactions, call `get_transactions(merchant_contains="coffee")`, assert 3 rows returned in row shape (no in-tool summarization).
- `tests/test_tools.py` — alignment test: `calculate_total(merchant_contains="Trader Joe's")` returns the same sum that a parallel `list_transactions` + manual sum would produce. This catches divergence between the two tools' filter handling.
- `tests/test_apply_transaction_filters.py` — direct unit test on the extracted filter helper. Parametrize over each filter field; assert one absent field produces no constraint.
- `tests/test_usage_cap.py` — seed `ai_call_log` with high `chat_turn` token counts dated today; assert the next turn raises `UsageCapExceeded` before any `messages.create()` fires. Counter-test: seed the same volume dated yesterday (UTC); the turn must proceed.
- `tests/test_usage_cap.py` — categorization-tokens-don't-count: seed `ai_call_log` rows with `task_type='categorization'` totaling above the cap; the turn must proceed (only `chat_turn` rows count).
- `tests/test_429_backoff.py` — mock the Anthropic client to raise `RateLimitError` once then return a normal response; assert the loop succeeds with exactly one retry. Mock to raise `RateLimitError` twice; assert `AgentLoopError("AI_PROVIDER_RATE_LIMITED")` propagates and no third call fires.

## Don't

- Don't register `propose_transaction`, `propose_card`, `propose_subscription`, or `set_goal` today. Those are 9b / Day 14 / Day 19 / 9b respectively. Registering an unimplemented tool means Claude calls something whose result is a user-visible error.
- Don't add `merchant_contains` to `calculate_total` as a special case; widen its schema to the full `TransactionFilters` shape via the shared filter builder.
- Don't add a `log_tool_call` middleware (see rationale above). The existing per-`messages.create()` `ai_call_log` row is sufficient.
- Don't check the cap between iterations. Entry-only, lenient thereafter.
- Don't query `ai_call_log_daily` for the cap. Today's data is only ever in raw `ai_call_log`.
- Don't catch generic `Exception` in `with_429_backoff`. Only `anthropic.RateLimitError`.
- Don't use `supabase_admin` anywhere in this day's code. Read paths use `supabase_for_user(user.jwt)`; the cap query is the same. Invariant 1 + invariant 14.
- Don't bypass the shared filter builder once it exists. Future tools that filter transactions must use it.

## Done when

- `pytest tests/test_tools.py tests/test_apply_transaction_filters.py tests/test_usage_cap.py tests/test_429_backoff.py` passes.
- A turn that asks "How much on dining this month?" calls `calculate_total` (not `get_transactions`) and returns the right number.
- A turn that asks "What did I spend at Trader Joe's last week?" calls `calculate_total(merchant_contains="Trader Joe's", date_from=...)` — proving the shared filter shape works end-to-end.
- A user whose `ai_call_log` shows they've already used the cap gets `DAILY_CAP_EXCEEDED` instead of any Anthropic call firing.
- A mocked `RateLimitError` on the first Anthropic call results in a 2-second pause and a second attempt; if that also 429s, the loop surfaces `AI_PROVIDER_RATE_LIMITED`.
- `ai_call_log.prompt_version` for any chat turn after this day reads `"chat_v2"`.
- `git grep -n 'propose_' app/agent/tools.py` returns nothing — proves no propose tool leaked into 9a.
