# Day 9b — propose_transaction, set_goal with safe-upsert, invariant-guard test

## Context

Day 9a shipped the read tools, the usage cap, the 429 wrapper, and a rewritten system prompt. This day adds the **propose-then-confirm write surface** for transactions — the load-bearing UX pattern from CLAUDE.md invariant 8 — plus the one carve-out (`set_goal`) for low-risk reversible direct writes.

`propose_card` and `propose_subscription` deliberately stay out of this day. They land on Day 14 (alongside the Perplexity lookup) and Day 19 (alongside the `pg_cron` auto-logger and the confirm endpoint). Registering them earlier would mean Claude sees them in its tool list, calls them when the user's message fits, and the user gets either an error tool-result or a 404 on confirm. Both are worse than the tool simply not being available.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow), §7.2 (propose-then-confirm rationale + `set_goal` carve-out), `CLAUDE.md` invariant 8.
- `app/models/transactions.py` — `TransactionProposal` already exists from Day 5. The tool returns this exact shape; do not redefine it.
- `app/integrations/gemini.py` — `categorize()` is what the propose-transaction tool calls for the category suggestion.
- `app/util/merchant.py` — `normalize_merchant()` is what the propose-transaction tool calls before returning the proposal so the merchant string lines up with the canonical form used elsewhere.

## Deliverables

### `propose_transaction` — `app/agent/tools.py`

```text
propose_transaction({merchant, amount, date, card_id?, category?, notes?}) → TransactionProposal
```

Tool implementation:

1. Normalize `merchant` via `normalize_merchant()` (Day 4).
2. If `category` is not provided, call `categorize(merchant, amount)` (Day 4 / Gemini) to fill it. If Gemini returns nothing usable, fall back to `"Other"` and populate `gemini_suggestion=None`. The `categorize()` call already writes its own `ai_call_log` row with `task_type='categorization'`, which does **not** count against the chat cap (Day 9a test confirms this).
3. If Claude passed a card name (string) instead of a `card_id` (UUID), resolve it via a `cards` lookup scoped by `supabase_for_user(user.jwt)`. If no unambiguous match exists, return the proposal with `card_id=None` and let the user pick on the parse card.
4. Generate a fresh `client_request_id = uuid4()` for offline-replay idempotency (DESIGN.md §8.2).
5. Return a `TransactionProposal` instance (imported from `app/models/transactions.py`).

**Critical:** the tool must not call `.insert()` on `transactions`. The whole point of propose-then-confirm is that no row exists until `POST /transactions/confirm` (Day 5) is called. The invariant-guard test below enforces this structurally.

### `set_goal` — `app/agent/tools.py` (the one direct-write carve-out)

```text
set_goal({category?, amount, period: 'week'|'month'|'year'}) → Goal
```

Goals are low-risk, reversible, and not on the transaction ledger (DESIGN.md §7.2), so the propose-confirm ceremony isn't worth it. But the tool must behave like a real "set" — calling `set_goal(category="Dining", amount=300, period="month")` after `set_goal(category="Dining", amount=400, period="month")` must **replace** the prior goal, not append a duplicate.

**Implementation:** `INSERT ... ON CONFLICT (user_id, COALESCE(category, ''), period) DO UPDATE SET amount = EXCLUDED.amount, updated_at = now() RETURNING *`. Via `supabase_for_user(user.jwt)`.

### Migration — `supabase/migrations/<timestamp>_goals.sql`

```sql
CREATE TABLE goals (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    category    text,
    amount      numeric     NOT NULL CHECK (amount > 0),
    period      text        NOT NULL CHECK (period IN ('week', 'month', 'year')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- "Latest wins" enforced at the DB layer, not in application code.
-- COALESCE handles the NULL-category case (an overall budget across categories).
CREATE UNIQUE INDEX goals_user_cat_period_uniq
    ON goals (user_id, COALESCE(category, ''), period);

ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals FORCE  ROW LEVEL SECURITY;

CREATE POLICY goals_owner_all ON goals
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE INDEX goals_user_idx ON goals (user_id);
```

The partial-index pattern (using `COALESCE(category, '')`) is required because Postgres treats `NULL` values as distinct in unique constraints by default. Without `COALESCE`, two `set_goal(category=NULL, period="month")` calls would create duplicate rows.

### System prompt — `app/prompts/chat.py`

Append a tool description for `propose_transaction` and `set_goal`. Critical prose for `propose_transaction`:

> When the user describes a purchase (a merchant + an amount), call `propose_transaction`. The tool returns a proposal payload — it does **not** add the transaction. After the call, tell the user something like "here's the parse — tap looks right to add it." Do not say "I've added it" or "added successfully" — the row only exists once the user taps the confirm button in the UI.

Bump `PROMPT_VERSION` to `"chat_v3"` (9a moved it to `chat_v2`).

### Invariant-guard test — `tests/test_tool_write_invariant.py`

The carve-out is the kind of thing future code can quietly expand. A structural test fails when the invariant is widened, regardless of which specific tool widened it.

```python
# Sketch — adapt to existing test fixtures
ALLOWED_DIRECT_WRITE_TOOLS = {"set_goal"}

@pytest.mark.parametrize("tool_name", list(TOOL_REGISTRY.keys()))
def test_tool_does_not_insert_unless_allowlisted(tool_name, fake_user, mock_supabase):
    if tool_name in ALLOWED_DIRECT_WRITE_TOOLS:
        pytest.skip("explicitly allowed to write")
    schema, executor = TOOL_REGISTRY[tool_name]
    args = minimal_args_from_schema(schema["input_schema"])
    try:
        executor(fake_user, **args)
    except Exception:
        pass  # The tool may fail without a real DB; we only care about insert attempts.
    for call in mock_supabase.mock_calls:
        assert ".insert(" not in str(call), (
            f"Tool {tool_name} called .insert() — violates invariant 8. "
            f"If intentional, add to ALLOWED_DIRECT_WRITE_TOOLS with rationale."
        )
```

The list `ALLOWED_DIRECT_WRITE_TOOLS = {"set_goal"}` is the single point of truth for the carve-out. Adding a tool to that set should require a PR comment explaining why the row is "low-risk and reversible" enough to skip the propose flow. The same pattern is used at `tests/test_no_service_role_leak.py` (referenced from `app/integrations/aicalllog.py:7`).

### Other tests

- `tests/test_tools.py` — `propose_transaction` integration test: invoke through the loop with a mocked Claude that emits the tool_use block. Assert the tool returns a valid `TransactionProposal` (with a fresh `client_request_id` and a non-empty `category`) and assert **no row appears in `transactions`** after the turn.
- `tests/test_tools.py` — `set_goal` idempotent overwrite: call twice with the same `(category, period)` and different amounts; assert only one row exists in `goals` and its `amount` matches the second call.
- `tests/test_tools.py` — `set_goal` with `category=None` upsert works (the COALESCE partial index handles NULL).
- `tests/test_rls.py` — user B cannot SELECT, UPDATE, or DELETE user A's `goals` rows.

## Don't

- Don't register `propose_card` or `propose_subscription`. Day 14 and Day 19 own those.
- Don't omit the `COALESCE` in the unique index — without it, NULL-category goals will duplicate.
- Don't make `set_goal` an append-only insert and rely on readers to pick the latest. The verb "set" implies overwrite; storage must match.
- Don't write to `transactions` from inside `propose_transaction`. The invariant-guard test will catch this; do not loosen the test.
- Don't change `TransactionProposal`'s shape in this day. The model is shared with Day 5's `POST /transactions/confirm` endpoint — drift between proposal and confirm bodies is the failure mode the shared class exists to prevent.

## Done when

- `pytest tests/test_tools.py tests/test_tool_write_invariant.py tests/test_rls.py` passes.
- "spent $47 at Trader Joe's on my Amex Gold" → `propose_transaction` is called → tool returns a `TransactionProposal` with a fresh `client_request_id` → **no row exists in `transactions`** until `POST /transactions/confirm` is separately called.
- "set my dining budget to $300/month" → `set_goal` upserts a single row → calling again with $250 updates the same row, doesn't create a duplicate.
- The invariant-guard test fails loudly if anyone adds an `add_transaction`-like tool that writes to a domain table.
- `ai_call_log.prompt_version` for chat turns after this day reads `"chat_v3"`.
