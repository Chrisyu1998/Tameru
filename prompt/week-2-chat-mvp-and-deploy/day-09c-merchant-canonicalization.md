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

A view is simpler and is the recommendation — **but** Postgres views default to running as the view owner, which silently bypasses RLS on the underlying `transactions` table. The view DDL must include `WITH (security_invoker = true)` (Postgres 15+) so the user's JWT context is what gets evaluated against the `auth.uid() = user_id` policy. Without that option set, the view returns every user's rows; the RLS-isolation test below catches this, but specify the option in the migration so it isn't a "test caught the regression" moment.

**Migration deliverable** (invariant 6 — schema changes go through `supabase/migrations/`):

Add a migration `supabase/migrations/<timestamp>_top_user_merchants_view.sql` creating the view with `security_invoker = true` and a comment pointing at this prompt. Do not edit the schema from the dashboard.

**Block format** (the string returned):

```text
The user's top merchants from the last 90 days, ordered by frequency:
- Kentucky Fried Chicken (12 visits, last 3 days ago)
- Trader Joe's (8 visits, last 1 day ago)
- ...

When the user mentions a merchant whose spelling closely matches one of these (KFC ≈ Kentucky Fried Chicken, TJs ≈ Trader Joe's), use the exact spelling from this list when calling propose_transaction. This keeps the user's history from fragmenting across spelling variants.
```

Token budget: ~300 tokens (30 lines × ~8 tokens + framing). Empty-merchants case (new user): return a one-line block ("(No prior merchants yet.)") so the block is always present and the system-prompt array shape doesn't depend on data state. Keep the empty-state copy minimal — "use the user's own spelling" is redundant with default behavior and just costs tokens on every cold-start turn.

**Call frequency:** once per turn, at turn entry — not per loop iteration. The merchant set doesn't change inside a turn. The query is one indexed read at v1 scale (~30 rows out of a few thousand), so per-turn-start invocation is the right cadence; no cross-turn cache needed.

### Cache-aware system prompt assembly

`render_system_prompt()` becomes `render_system_prompt(user_jwt, today=None)` and returns a list of content blocks instead of a string:

```python
def render_system_prompt(
    user_jwt: str,
    today: _dt.date | None = None,
) -> list[dict[str, Any]]:
    if today is None:
        today = _dt.date.today()
    dynamic_tail = (
        f"Today is {today.isoformat()}.\n\n"
        + render_user_merchants(user_jwt)
    )
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_tail,
        },
    ]
```

The `cache_control` marker on the first block tells Anthropic to hash the prefix up to that point and store the attention state for 5 minutes. The second block (date + per-user merchants) lives outside the cache. The static preamble is identical across users, so all users share the same cached prefix and the §11.3 cost projection's 90%-cache-read discount applies.

The `Today is ...` line must stay in the dynamic tail — `chat_v2` and `chat_v3` carry it (current `render_system_prompt` at `app/prompts/chat.py:117-139`) so Claude can resolve "today", "last week", and the `date` arg on `propose_transaction`. Dropping it is a silent regression. It cannot live in the cached block (the date changes daily and would bust the cache every midnight UTC), so it joins the merchants block in the uncached tail.

**Hash semantics — read this carefully.** `system_prompt_hash` (at `app/prompts/chat.py:142`) currently hashes the rendered string plus tool schemas. The whole purpose of `ai_call_log.prompt_hash` (per the docstring at `chat.py:21-26`) is to bucket eval and cost queries across heterogeneous prompts. If you fold the per-user merchants block (or the date line) into the hash, every user — and the same user across days as their merchants list drifts — gets a different `prompt_hash` for the same `chat_v4` prompt, and the bucketing collapses.

So `system_prompt_hash` continues to hash **only the static preamble (block 0's text) + tool schemas**. The dynamic tail is deliberately excluded. Update the signature to take the block list and pluck `blocks[0]["text"]` rather than passing it the joined string.

`app/agent/loop.py:161` changes from:

```python
system = render_system_prompt()
prompt_hash = system_prompt_hash(system, schemas)
```

to:

```python
system = render_system_prompt(user_jwt=user.jwt)
prompt_hash = system_prompt_hash(system, schemas)  # hashes block[0] only
```

The `messages.create(system=system, ...)` call now passes the block list directly — Anthropic accepts either a string or a list-of-blocks for `system`.

Bump `PROMPT_VERSION` to `"chat_v4"`.

### Tests

- `tests/test_render_user_merchants.py` — seed 5 transactions with merchant `"Kentucky Fried Chicken"` for user A; assert `render_user_merchants(user_a.jwt)` contains that exact string and orders it ahead of single-visit merchants. Counter-test: seed nothing; assert the empty-merchants block is returned (not an empty string).
- `tests/test_render_user_merchants.py` — ordering determinism: seed two merchants with equal frequency but different `MAX(date)`; assert the more recent one ranks higher.
- `tests/test_render_user_merchants.py` — RLS isolation: user B's transactions must not appear in user A's block. This is the test that catches a missing `security_invoker = true` on the view.
- `tests/test_prompt_cache.py` — call `render_system_prompt(user_a.jwt)` and `render_system_prompt(user_b.jwt)`; assert `block[0]` has identical text and identical `cache_control`, and `block[1]` differs. (This catches the "I accidentally put user data in the cached block" failure mode.)
- `tests/test_prompt_cache.py` — date line: call `render_system_prompt(user_a.jwt, today=date(2026, 5, 14))`; assert `"Today is 2026-05-14."` appears in `block[1]["text"]` and is **not** present in `block[0]["text"]`. (Regression guard: if the dynamic-tail wiring breaks, dates silently disappear and Claude starts inventing them from training distribution.)
- `tests/test_prompt_cache.py` — hash stability across users: assert `system_prompt_hash(render_system_prompt(user_a.jwt), schemas) == system_prompt_hash(render_system_prompt(user_b.jwt), schemas)` (same `chat_v4` prompt → same hash regardless of per-user merchants). Counter-assertion: change one tool schema; hash must differ. This is the invariant that keeps `ai_call_log.prompt_hash` useful for eval bucketing.
- `tests/test_chat_turn_merchants.py` — end-to-end: seed `"Kentucky Fried Chicken"` for user A; run a chat turn with input `"spent $10 at KFC"` against a mocked Claude that echoes its system prompt back; assert the merchant block is present in the system field of the captured `messages.create` call, and assert the captured `system` is a list of two blocks (not a string).

## Don't

- Don't put `render_user_merchants()` output inside the cached block. The block ordering matters — static first (with `cache_control`), per-user second (no `cache_control`). If you swap them, every user gets a cold cache.
- Don't put the `Today is ...` line in the cached block. It changes daily and would bust the cache every midnight UTC. It belongs in the dynamic tail next to the merchants block.
- Don't drop the `Today is ...` line entirely. `chat_v2` and `chat_v3` carry it; without it, Claude invents dates from training distribution and `propose_transaction(date=...)` lands in the past.
- Don't fold the dynamic tail (date or merchants) into `system_prompt_hash`. The hash exists to bucket eval and cost queries; per-user variance breaks that grouping. Hash `block[0]["text"]` + tool schemas only.
- Don't create the view without `WITH (security_invoker = true)`. The default is owner-execution, which bypasses RLS and returns every user's rows. The RLS-isolation test will catch it, but the migration should be right the first time.
- Don't call `render_user_merchants()` more than once per turn. Once at turn entry; the result is stable for the duration of the turn.
- Don't query the `transactions` table directly from inside the loop on every iteration. Build the merchant block once, pass it through.
- Don't use the service role for the merchant query. `supabase_for_user(user_jwt)` only (invariant 1).
- Don't omit the empty-state block. A user with zero transactions must still get a system prompt with two blocks; the second is the empty-merchants framing string. Otherwise downstream code branching on "is there a merchants block" gets messy.

## Done when

- `pytest tests/test_render_user_merchants.py tests/test_prompt_cache.py tests/test_chat_turn_merchants.py` passes.
- A user with `"Kentucky Fried Chicken"` in their history who types `"spent $10 at KFC"` gets `propose_transaction(merchant="Kentucky Fried Chicken", ...)` — not `propose_transaction(merchant="KFC", ...)`.
- Inspecting the `messages.create()` call shows `system` as a list of two blocks, the first with `cache_control: {type: "ephemeral"}` and the second without. The second block contains both the `Today is ...` line and the merchants block.
- `ai_call_log.prompt_hash` for two different users on the same day is **identical** (proves per-user content stays out of the hash); changing `SYSTEM_PROMPT` or any tool schema changes it.
- The Anthropic SDK's `response.usage.cache_read_input_tokens` is non-zero on the second consecutive turn within 5 minutes for the same user (proves the cache is being hit). At v1 scale this is a manual smoke check; an automated assertion is fine but not required.
- `ai_call_log.prompt_version` for chat turns after this day reads `"chat_v4"`.
- The new migration is in `supabase/migrations/` and the view DDL includes `WITH (security_invoker = true)`.
