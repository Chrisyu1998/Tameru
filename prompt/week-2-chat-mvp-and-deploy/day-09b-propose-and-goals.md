# Day 9b — propose_transaction, set_goal with safe-upsert, invariant-guard test

## Context

Day 9a shipped the read tools, the usage cap, the 429 wrapper, and a rewritten system prompt. This day adds the **propose-then-confirm write surface** for transactions — the load-bearing UX pattern from CLAUDE.md invariant 8 — plus the one carve-out (`set_goal`) for low-risk reversible direct writes.

`propose_card` and `propose_subscription` deliberately stay out of this day. They land on Day 14 (alongside the Perplexity lookup) and Day 19 (alongside the `pg_cron` auto-logger and the confirm endpoint). Registering them earlier would mean Claude sees them in its tool list, calls them when the user's message fits, and the user gets either an error tool-result or a 404 on confirm. Both are worse than the tool simply not being available.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow), §7.2 (propose-then-confirm rationale + `set_goal` carve-out), §8 (schema doctrine). `CLAUDE.md` invariant 8.
- `app/models/transactions.py` — `TransactionProposal` already exists from Day 5. The tool returns this exact shape; do not redefine it.
- `app/integrations/gemini.py:71` — `categorize(merchant, user) -> CategorySuggestion`. **Note the signature: two positional args (`merchant`, `user`). Amount is not a parameter — see the `categorize_v3` rationale in `app/prompts/categorize.py`.** The function writes its own `ai_call_log` row with `task_type='categorization'`, which does not count against the chat cap (Day 9a test confirms this).
- `app/routes/transactions.py:97-101` — the confirm endpoint's "learning loop" that fires only when `gemini_suggestion != category`. This is what makes `gemini_suggestion` semantics load-bearing (see deliverable below).
- `app/util/merchant.py` — `normalize_merchant()` is what the propose-transaction tool calls before returning the proposal so the merchant string lines up with the canonical form used elsewhere. `categorize()` already calls this internally, so don't double-normalize before passing to `categorize()` — call it once on the proposal merchant after the categorize call returns.
- `app/agent/tools.py` — follow the Day 9a doctrine: every tool has a `<Name>Request` Pydantic class with `model_config = ConfigDict(extra="forbid")`, a module-level `<NAME>_TOOL` schema dict, and an executor function. The `TOOL_REGISTRY` dict pairs schemas to executors; the loop only sees what's registered there.

## Deliverables

### `propose_transaction` — `app/agent/tools.py`

**Tool input schema (UUID-only for `card_id`):**

```text
propose_transaction({
    merchant: string,
    amount: number,
    date: string (YYYY-MM-DD),
    card_id?: string (UUID),     # MUST be a UUID, not a card name
    category?: string,            # closed enum (ALLOWED_CATEGORIES)
    notes?: string
}) → TransactionProposal
```

`card_id` is UUID-only by design. If the user names a card ("on my Amex Gold"), Claude is expected to call `get_cards` first (already available from Day 9a), read the returned UUIDs from the in-context `tool_result`, then call `propose_transaction(card_id=<uuid>)`. The system-prompt update below tells Claude this explicitly. Rationale: keeping the tool input tightly typed lets the agent loop reason about ambiguity (two cards both nicknamed "Amex") in chat by asking a clarifying question, rather than having the tool silently pick one.

**Pydantic model — `ProposeTransactionRequest`:**

Follow the Day 9a pattern. `merchant`, `amount`, `date` required; `card_id`, `category`, `notes` optional. `extra="forbid"`. Re-use the `category` validator from `TransactionProposal` (closed enum). Do not import `TransactionProposal` as the request model — request and response have different optionality (request `category` is optional, response `category` is required after the tool fills it).

**Tool implementation:**

1. Validate the input via `ProposeTransactionRequest.model_validate(kwargs)`.
2. Resolve the category:
   - If the user already supplied `category` (Claude pre-filled from explicit text like "spent $7 on coffee at Blue Bottle"), accept it as-is. Set `gemini_suggestion=None` — there is no Gemini baseline to learn against in this branch, and that is the correct semantic.
   - Otherwise call `categorize(merchant, user)`. On success, set **both** `category = suggestion.category` AND `gemini_suggestion = suggestion.category`. The two fields start equal; they diverge only if the user edits on the parse card before tapping confirm. That divergence is the training signal the learning loop at `app/routes/transactions.py:97-101` consumes — without `gemini_suggestion` carrying Gemini's frozen guess, the loop never fires from chat-originated rows.
   - If `categorize()` raises `GeminiError` (any subclass), fall back to `category="Other"` and `gemini_suggestion=None`. The categorize call already wrote its own `ai_call_log` failure row before raising, so no additional logging here.
3. Defensive card_id check: if `card_id` is not None, do a quick RLS-scoped `SELECT id FROM cards WHERE id = ? LIMIT 1` via `supabase_for_user(user.jwt)`. If the lookup returns no rows (hallucinated UUID, deleted card, or someone else's UUID — RLS will return empty for the latter), drop `card_id` to `None` rather than echoing it back. Rationale: the confirm endpoint's `_assert_card_owned` would 403 on a bad UUID after the user tapped "looks right" — degrading to "pick a card on the parse card" is a strictly better failure mode than an error at commit time.
4. Normalize merchant: `merchant = normalize_merchant(merchant)`. This must happen after the categorize call (categorize normalizes internally; the proposal carries the canonical lower-cased form).
5. Mint a fresh `client_request_id = uuid4()` for offline-replay idempotency (DESIGN.md §8.2).
6. Return a `TransactionProposal` instance (imported from `app/models/transactions.py`). Serialize via `.model_dump(mode="json")` so dates and UUIDs become strings — the loop's `json.dumps(tool_result)` step needs plain JSON.

**Critical:** the tool must not call `.insert()`, `.upsert()`, `.update()`, `.delete()`, or `.rpc()` on `transactions`. The whole point of propose-then-confirm is that no row exists until `POST /transactions/confirm` (Day 5) is called. The invariant-guard test below enforces this structurally.

### `set_goal` — `app/agent/tools.py` (the one direct-write carve-out)

```text
set_goal({category?, amount, period: 'week'|'month'|'year'}) → Goal
```

Goals are low-risk, reversible, and not on the transaction ledger (DESIGN.md §7.2), so the propose-confirm ceremony isn't worth it. But the tool must behave like a real "set" — calling `set_goal(category="Dining", amount=300, period="month")` after `set_goal(category="Dining", amount=400, period="month")` must **replace** the prior goal, not append a duplicate.

**Pydantic models:**

- `SetGoalRequest` — `category: str | None` (validated against `ALLOWED_CATEGORIES` when present), `amount: Decimal > 0`, `period: Literal["week", "month", "year"]`. `extra="forbid"`.
- `Goal` — response model mirroring the row: `id: UUID`, `user_id: UUID`, `category: str | None`, `amount: Decimal`, `period: str`, `created_at: datetime`, `updated_at: datetime`.

**Implementation:** PostgREST upsert via `supabase_for_user(user.jwt)`:

```python
client = supabase_for_user(user.jwt)
resp = (
    client.table("goals")
    .upsert(
        {
            "user_id": str(user.user_id),
            "category": request.category,   # may be None
            "amount": str(request.amount),
            "period": request.period,
        },
        on_conflict="user_id,category,period",
    )
    .execute()
)
return Goal.model_validate(resp.data[0]).model_dump(mode="json")
```

`on_conflict` references the unique constraint by its column list. For this to handle the NULL-category case correctly, the constraint must be declared `NULLS NOT DISTINCT` — see migration below.

### Migration — `supabase/migrations/<timestamp>_goals.sql`

```sql
CREATE TABLE goals (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    category    text,
    amount      numeric     NOT NULL CHECK (amount > 0),
    period      text        NOT NULL CHECK (period IN ('week', 'month', 'year')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- NULLS NOT DISTINCT folds NULL-category rows into the same uniqueness
    -- bucket. Without it, two set_goal(category=NULL, period='month') calls
    -- would both insert (Postgres's default semantics treat NULL as distinct
    -- in unique constraints). Requires Postgres 15+; Supabase runs 15+.
    -- A named CONSTRAINT (not just a UNIQUE INDEX) is required so the
    -- PostgREST client's `on_conflict="user_id,category,period"` upsert
    -- query parameter resolves to it.
    CONSTRAINT goals_user_cat_period_uniq
        UNIQUE NULLS NOT DISTINCT (user_id, category, period)
);

ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals FORCE  ROW LEVEL SECURITY;

CREATE POLICY goals_owner_all ON goals
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE INDEX goals_user_idx ON goals (user_id);
```

"Latest wins" is enforced by the unique constraint plus the upsert — encoded once at the schema, not in every reader. The dashboard, the weekly digest, and any future chat tool that reads goals can all do a plain `SELECT amount FROM goals WHERE category=? AND period=?` and trust there is at most one row.

### DESIGN.md §8 — add a `goals` table entry

CLAUDE.md "Keeping DESIGN.md in sync" applies: this PR adds a new table, so add a §8.x section documenting `goals` (columns, RLS, the `NULLS NOT DISTINCT` rationale, latest-wins semantics). One paragraph plus a column table is enough; mirror the style of the existing §8 entries.

### System prompt — `app/prompts/chat.py`

Two changes, not just an append:

1. **Replace** the "you cannot add, edit, or delete" paragraph (currently lines 88-91 in `chat_v2`). The new tools contradict it. The replacement should say something like:

   > You can propose new transactions for the user via `propose_transaction`, and you can set spending goals directly via `set_goal`. You cannot edit or delete existing transactions, cards, or subscriptions — direct the user to the edit sheet (transactions list → tap a row) for those.

2. **Append** tool descriptions for `propose_transaction` and `set_goal`. Critical prose for `propose_transaction`:

   > When the user describes a purchase (a merchant + an amount), call `propose_transaction`. The tool returns a proposal payload — it does **not** add the transaction. After the call, tell the user something like "here's the parse — tap looks right to add it." Do not say "I've added it" or "added successfully" — the row only exists once the user taps the confirm button in the UI.
   >
   > If the user names a card ("on my Amex Gold"), call `get_cards` first to look up the UUID, then pass `card_id=<uuid>` to `propose_transaction`. Do not call `get_cards` more than once per turn — reuse the result already in your context. If two cards match the name ambiguously, ask the user which one before proposing.

Critical prose for `set_goal`:

   > Setting a goal **replaces** any prior goal for the same (category, period). Calling `set_goal(category="Dining", amount=300, period="month")` after a prior `$400/month` goal updates the existing goal — it does not add a second one. To set an overall budget across all categories, omit `category`.

Bump `PROMPT_VERSION` to `"chat_v3"` (9a moved it to `chat_v2`). Add the chat_v3 line to the version-log docstring at the top of `chat.py`.

### Invariant-guard test — `tests/contracts/test_tool_write_invariant.py`

The carve-out is the kind of thing future code can quietly expand. A structural test fails when the invariant is widened, regardless of which specific tool widened it.

```python
# Sketch — adapt to existing fixtures in tests/conftest.py
FORBIDDEN_WRITE_METHODS = (".insert(", ".upsert(", ".update(", ".delete(", ".rpc(")
ALLOWED_DIRECT_WRITE_TOOLS = {"set_goal"}

@pytest.mark.parametrize("tool_name", list(TOOL_REGISTRY.keys()))
def test_tool_does_not_write_unless_allowlisted(tool_name, authed_user_a, monkeypatch):
    """Tool executors must not mutate the DB unless explicitly allow-listed.

    Mocks supabase_for_user so we can observe every Supabase client call
    without hitting real Postgres. Any `.insert(`, `.upsert(`, `.update(`,
    `.delete(`, or `.rpc(` from a non-allowlisted tool fails this test.
    """
    if tool_name in ALLOWED_DIRECT_WRITE_TOOLS:
        pytest.skip("explicitly allowed to write")
    recorded: list[str] = []
    fake_client = _RecordingClient(recorded)  # tiny harness; see test file for impl
    monkeypatch.setattr("app.agent.tools.supabase_for_user", lambda jwt: fake_client)

    schema, executor = TOOL_REGISTRY[tool_name]
    args = _minimal_args_from_schema(schema["input_schema"])
    try:
        executor(authed_user_a, **args)
    except Exception:
        pass  # tool may legitimately fail without a real DB; we only check writes.

    for call in recorded:
        for forbidden in FORBIDDEN_WRITE_METHODS:
            assert forbidden not in call, (
                f"Tool {tool_name} called {forbidden} — violates CLAUDE.md "
                f"invariant 8. If intentional, add to ALLOWED_DIRECT_WRITE_TOOLS "
                f"with rationale."
            )
```

`ALLOWED_DIRECT_WRITE_TOOLS = {"set_goal"}` is the single point of truth for the carve-out. Adding a tool to that set should require a PR comment explaining why the row is "low-risk and reversible" enough to skip the propose flow. The same parametrize-over-registry pattern is used at `tests/contracts/test_no_service_role_leak.py`.

### Other tests

- `tests/test_tools.py` — `propose_transaction` integration test: invoke through the loop with a mocked Claude that emits the tool_use block. Assert the tool returns a valid `TransactionProposal` (with a fresh `client_request_id`, a non-empty `category`, a `gemini_suggestion` equal to the Gemini result when categorize was called) and assert **no row appears in `transactions`** after the turn.
- `tests/test_tools.py` — `propose_transaction` with Claude pre-filling `category`: assert `gemini_suggestion` is `None` (no Gemini call, no learning baseline).
- `tests/test_tools.py` — `propose_transaction` with a hallucinated `card_id`: pass a random UUID that isn't in the user's cards table; assert the returned proposal has `card_id=None`.
- `tests/test_tools.py` — `propose_transaction` with a card belonging to user B (RLS path): user A's tool call with user B's `card_id`; assert `card_id=None` in the proposal. RLS makes this look identical to the hallucinated-UUID case from the tool's perspective — which is the property we want.
- `tests/test_tools.py` — `set_goal` idempotent overwrite: call twice with the same `(category, period)` and different amounts; assert only one row exists in `goals` and its `amount` matches the second call.
- `tests/test_tools.py` — `set_goal` with `category=None` upsert works (the `NULLS NOT DISTINCT` constraint folds the NULL bucket correctly): two calls with `category=None, period="month"` produce one row.
- `tests/test_tools.py` — `set_goal` across different `(category, period)` slots coexist: `(Dining, month)` and `(Dining, year)` and `(Groceries, month)` and `(None, month)` all produce four distinct rows.
- `tests/contracts/test_rls.py` — user B cannot SELECT, UPDATE, or DELETE user A's `goals` rows.
- `tests/test_tools.py` — registry sanity test (from Day 9a) needs to update its expected set to include `propose_transaction` and `set_goal`. The 9a assertion ("only read tools") was the structural alarm for 9b — flipping it deliberately is the right move.

## Don't

- Don't register `propose_card` or `propose_subscription`. Day 14 and Day 19 own those.
- Don't make `card_id` accept a string card name. Claude resolves names via `get_cards` first. The tool input stays UUID-only.
- Don't drop `gemini_suggestion` on the success path. It must equal `category` after a Gemini call so the override-learning loop at `app/routes/transactions.py:97-101` can fire on user edits.
- Don't use a functional unique index with `COALESCE(category, '')` — PostgREST's `on_conflict` parameter cannot reference functional expressions. Use `UNIQUE NULLS NOT DISTINCT (...)` instead.
- Don't make `set_goal` an append-only insert and rely on readers to pick the latest. The verb "set" implies overwrite; storage must match.
- Don't write to `transactions` from inside `propose_transaction`. The invariant-guard test will catch this; do not loosen the test.
- Don't change `TransactionProposal`'s shape in this day. The model is shared with Day 5's `POST /transactions/confirm` endpoint — drift between proposal and confirm bodies is the failure mode the shared class exists to prevent.
- Don't keep the "you cannot add, edit, or delete..." paragraph in the system prompt as-is. It contradicts the new tools — rewrite it per the system-prompt deliverable above.

## Done when

- `pytest tests/test_tools.py tests/contracts/test_tool_write_invariant.py tests/contracts/test_rls.py` passes.
- "spent $47 at Trader Joe's on my Amex Gold" → Claude calls `get_cards` (or reuses an in-context result), then `propose_transaction` → tool returns a `TransactionProposal` with a fresh `client_request_id`, `category` from Gemini, `gemini_suggestion` equal to `category` → **no row exists in `transactions`** until `POST /transactions/confirm` is separately called.
- The same flow with a hallucinated `card_id`: tool returns a proposal with `card_id=None` (verified by the test above).
- User edits the category on the parse card before confirming → confirm endpoint upserts a `merchant_category` row → next time the same merchant appears in chat, Gemini gets a past-corrections hint and suggests the corrected category.
- "set my dining budget to $300/month" → `set_goal` upserts a single row → calling again with $250 updates the same row, doesn't create a duplicate.
- "set an overall budget of $2000/month" (no category) → upserts a row with `category=NULL` → calling again updates the same row, even though the category is NULL.
- The invariant-guard test fails loudly if anyone adds an `add_transaction`-like tool that writes to a domain table, or uses `.upsert(` / `.update(` / `.delete(` / `.rpc(` from a non-allowlisted tool.
- `ai_call_log.prompt_version` for chat turns after this day reads `"chat_v3"`.
- `DESIGN.md` §8 contains a `goals` table section with column list, RLS, the `NULLS NOT DISTINCT` rationale, and the latest-wins semantic.
