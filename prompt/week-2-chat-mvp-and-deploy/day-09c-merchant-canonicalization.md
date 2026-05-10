# Day 9c — render_user_merchants + cache-aware system prompt assembly

## Context

Day 9a rewrote the system prompt as a single static string with `PROMPT_VERSION="chat_v2"` and Day 9b appended `propose_transaction` and `set_goal` descriptions for `"chat_v3"`. The prompt has been static across users — perfectly cacheable by Anthropic's prompt-cache mechanism.

This day adds the first **per-user** system-prompt block: `render_user_merchants`, which lists a user's top 30 merchants by frequency and recency so Claude canonicalizes "KFC" to the existing "Kentucky Fried Chicken" instead of fragmenting the user's history.

Adding per-user content to the system prompt is where prompt caching gets dangerous. If the entire system prompt is one string, per-user variation invalidates the cache for everyone and the §11.3 cost projection's 90%-cached-read discount disappears. The fix is to assemble the `system` field as a **two-block array** with a `cache_control` breakpoint between the static preamble (cached) and the per-user merchants block (uncached).

## Read first

- `DESIGN.md` §3.4 (merchant-merge cleanup rationale — why this lives on the chat write path), §7.7 (NL entry text+voice), §11.1 (prompt-cache token math).
- Anthropic prompt-caching docs — the cache breakpoint syntax (`{"type": "text", "text": ..., "cache_control": {"type": "ephemeral"}}`).
- `CLAUDE.md` invariant 8 (chat is the only create surface — this is why canonicalization must happen on the write path).

## Deliverables

### `render_user_merchants(user_jwt) -> str` — new function in `app/prompts/chat.py` (or a sibling module)

Returns a system-prompt block listing this user's top 30 merchants by frequency over the last 90 days, ties broken by recency.

**Query (via `supabase_for_user(user_jwt)`):**

```sql
SELECT merchant,
       COUNT(*) AS freq_90d,
       MAX(date) AS last_seen
FROM transactions
WHERE user_id = auth.uid()
  AND date >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY merchant
ORDER BY COUNT(*) DESC, MAX(date) DESC
LIMIT 30;
```

The PostgREST equivalent uses an RPC or a view; `supabase-py` does not compose this in pure builder calls. Pick one of:
- A SQL view `top_user_merchants` with RLS that exposes the per-user top 30.
- A Postgres function `top_merchants_for_user(window_days int)` called via `client.rpc(...)`.

A view is simpler and benefits from RLS-by-default; recommend the view.

**Block format** (the string returned):

```text
The user's top merchants from the last 90 days, ordered by frequency:
- Kentucky Fried Chicken (12 visits, last 3 days ago)
- Trader Joe's (8 visits, last 1 day ago)
- ...

When the user mentions a merchant whose spelling closely matches one of these (KFC ≈ Kentucky Fried Chicken, TJs ≈ Trader Joe's), use the exact spelling from this list when calling propose_transaction. This keeps the user's history from fragmenting across spelling variants.
```

Token budget: ~300 tokens (30 lines × ~8 tokens + framing). Empty-merchants case (new user): return a one-line block ("No prior merchants yet — use the user's own spelling when calling propose_transaction.") so the block is always present and the system-prompt array shape doesn't depend on data state.

**Call frequency:** once per turn, at turn entry — not per loop iteration. The merchant set doesn't change inside a turn. The query is one indexed read at v1 scale (~30 rows out of a few thousand), so per-turn-start invocation is the right cadence; no cross-turn cache needed.

### Cache-aware system prompt assembly

`render_system_prompt()` becomes `render_system_prompt(user_jwt)` and returns a list of content blocks instead of a string:

```python
def render_system_prompt(user_jwt: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": render_user_merchants(user_jwt),
        },
    ]
```

The `cache_control` marker on the first block tells Anthropic to hash the prefix up to that point and store the attention state for 5 minutes. The second block (per-user merchants) lives outside the cache. The static preamble is identical across users, so all users share the same cached prefix and the §11.3 cost projection's 90%-cache-read discount applies.

`system_prompt_hash` (at `app/prompts/chat.py:66`) currently takes the rendered string. Adapt it to accept the list and canonicalize via `json.dumps(..., sort_keys=True)` over the block sequence so the hash is stable across runs. The hash still goes into `ai_call_log.prompt_hash`.

`app/agent/loop.py:175` changes from:

```python
system = render_system_prompt()
```

to:

```python
system = render_system_prompt(user_jwt=user.jwt)
```

The `messages.create(system=system, ...)` call now passes the block list directly — Anthropic accepts either a string or a list-of-blocks for `system`.

Bump `PROMPT_VERSION` to `"chat_v4"`.

### Tests

- `tests/test_render_user_merchants.py` — seed 5 transactions with merchant `"Kentucky Fried Chicken"` for user A; assert `render_user_merchants(user_a.jwt)` contains that exact string and orders it ahead of single-visit merchants. Counter-test: seed nothing; assert the empty-merchants block is returned (not an empty string).
- `tests/test_render_user_merchants.py` — ordering determinism: seed two merchants with equal frequency but different `MAX(date)`; assert the more recent one ranks higher.
- `tests/test_render_user_merchants.py` — RLS isolation: user B's transactions must not appear in user A's block.
- `tests/test_prompt_cache.py` — call `render_system_prompt(user_a)` and `render_system_prompt(user_b)`; assert block[0] has identical text and identical `cache_control`, and block[1] differs. (This catches the "I accidentally put user data in the cached block" failure mode.)
- `tests/test_chat_turn_merchants.py` — end-to-end: seed `"Kentucky Fried Chicken"` for user A; run a chat turn with input `"spent $10 at KFC"` against a mocked Claude that echoes its system prompt back; assert the merchant block is present in the system field of the captured `messages.create` call.

## Don't

- Don't put `render_user_merchants()` output inside the cached block. The block ordering matters — static first (with `cache_control`), per-user second (no `cache_control`). If you swap them, every user gets a cold cache.
- Don't call `render_user_merchants()` more than once per turn. Once at turn entry; the result is stable for the duration of the turn.
- Don't query the `transactions` table directly from inside the loop on every iteration. Build the merchant block once, pass it through.
- Don't use the service role for the merchant query. `supabase_for_user(user_jwt)` only (invariant 1).
- Don't omit the empty-state block. A user with zero transactions must still get a system prompt with two blocks; the second is the empty-merchants framing string. Otherwise downstream code branching on "is there a merchants block" gets messy.

## Done when

- `pytest tests/test_render_user_merchants.py tests/test_prompt_cache.py tests/test_chat_turn_merchants.py` passes.
- A user with `"Kentucky Fried Chicken"` in their history who types `"spent $10 at KFC"` gets `propose_transaction(merchant="Kentucky Fried Chicken", ...)` — not `propose_transaction(merchant="KFC", ...)`.
- Inspecting the `messages.create()` call shows `system` as a list of two blocks, the first with `cache_control: {type: "ephemeral"}` and the second without.
- The Anthropic SDK's `response.usage.cache_read_input_tokens` is non-zero on the second consecutive turn within 5 minutes for the same user (proves the cache is being hit). At v1 scale this is a manual smoke check; an automated assertion is fine but not required.
- `ai_call_log.prompt_version` for chat turns after this day reads `"chat_v4"`.
