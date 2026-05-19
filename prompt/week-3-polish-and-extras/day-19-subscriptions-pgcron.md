# Day 19 — Subscription manager + `pg_cron` auto-logger with idempotency

## Goal

User adds a subscription (with or without a card backing it). Daily `pg_cron` SQL function inserts a transaction whenever `next_billing_date <= today` and advances `next_billing_date`. Idempotent under retries. Survives Railway deploys (because it doesn't run in FastAPI). Forward-only — past billing cycles are never backfilled.

This day also lands the `propose_subscription` agent tool and registers it in `TOOL_REGISTRY`. The tool registration deliberately waited until the `POST /subscriptions/confirm` endpoint exists — registering a tool whose confirm endpoint returns 404 means the user taps "looks right" on a perfect-looking parse card and gets a backend error. Tools that can't end-to-end commit are not in the registry.

## Read first

- `DESIGN.md` §6.5 (subscription auto-logger), §8.3 (subscriptions schema — `card_id` nullable, `client_request_id`, forward-only auto-log, immutability of `frequency` / `start_date`, split-cascade on card soft-delete), §14.3 (pg_cron rationale).
- `CLAUDE.md` invariants 4 (pg_cron only) and 8 (propose-then-confirm).
- Day 5's `client_request_id` idempotency pattern ([app/routes/transactions.py](app/routes/transactions.py) confirm path + the `transactions_user_client_request_id_unique` partial index in [supabase/migrations/](supabase/migrations/)). Subscriptions adopt the same shape — see "Why subscriptions get idempotency where cards don't" below.
- Day 15's offline confirm queue ([frontend/src/lib/offline_queue.ts](frontend/src/lib/offline_queue.ts)) — extended here to add the `"subscription"` `kind`.

## Why `client_request_id` on subscriptions

After the Day 15 cards follow-up (migration `20260517120000_cards_client_request_id.sql`), every ledger-adjacent table that flows through propose-then-confirm now carries a `client_request_id`. Same name across all three; **different roles depending on whether a natural uniqueness key exists**:

| Table | Natural key? | Role of `client_request_id` |
|---|---|---|
| `transactions` | None — two coffees at the same place on the same day on the same card is a normal pattern | Idempotency token + chat-rehydrate join key. Same-crid replay returns the existing row. |
| `cards` | `(user_id, issuer, last_four)` is structurally unique (one physical card per user per issuer) | Chat-rehydrate join key (disambiguates two same-name cards) + same-crid replay shortcut. The natural-key 409 still owns the "different proposals for the same physical card" case. |
| `subscriptions` | **None** — a family plan and a personal plan on the same card with the same name and frequency are both valid; and cardless ACH subscriptions can have any shape | Idempotency token + chat-rehydrate join key. Same shape as transactions. |

For subscriptions specifically, the Day 15 offline-queue + pg_cron-auto-logger combination amplifies the cost of a duplicate row:

- A duplicated subscription gets independently auto-logged by `pg_cron` every billing cycle (the `UNIQUE (subscription_id, date)` index is keyed on `subscription_id`, so two dup subscription rows produce two transaction rows per month).
- The recovery cost is not "one delete" (as for cards) but "delete the dup subscription + delete N already-auto-logged duplicate transactions," and N grows with months-until-the-user-notices.

So subscriptions get the full transactions-style treatment: nullable `client_request_id` column, partial unique index, route-level same-crid short-circuit. The `propose_subscription` tool mints a fresh UUID per call exactly the way `propose_card` (post-Day-15) and `propose_transaction` already do — copy that pattern from [app/agent/tools.py](app/agent/tools.py).

## Why `card_id` is nullable on subscriptions

The most common recurring charges for many users aren't card-funded — rent and mortgage are almost always bank ACH, utilities and phone bills often are, gym memberships frequently come from checking. The original `card_id NOT NULL` constraint silently excluded these from the auto-logger and forced the user to log them manually every month, which is exactly the friction the subscription manager is supposed to remove.

The change is contained: the [`subscriptions` table](supabase/migrations/20260421120200_subscriptions.sql) drops `NOT NULL` on `card_id`; `transactions.card_id` is already nullable ([20260421120300_transactions.sql:9](supabase/migrations/20260421120300_transactions.sql#L9)) so the auto-logger writes through with no change. Entry-moment insight skips cardless auto-logs (no card-mismatch suggestion is possible — fine). The Day 19b card-annual-fee dual-write requires a card by construction (it lives inside `POST /cards/confirm`), so AF tracking is unaffected.

## Deliverables

### Migrations

- **New migration** `20260518130000_subscriptions_card_id_nullable.sql`:
  - `ALTER TABLE subscriptions ALTER COLUMN card_id DROP NOT NULL;`
  - Comment in the migration body explaining the cardless-bills rationale (rent/utilities/ACH).

- **New migration** `20260518130100_subscriptions_client_request_id.sql`:
  - `ALTER TABLE subscriptions ADD COLUMN client_request_id UUID;` (nullable — pg_cron-written rows leave it NULL, and the partial index excludes them).
  - `CREATE UNIQUE INDEX subscriptions_user_client_request_id_unique ON subscriptions (user_id, client_request_id) WHERE client_request_id IS NOT NULL;`
  - DESIGN.md §8.3 already includes the new column row.

- **New migration** `20260518130200_subscription_autolog_function.sql`:
  - `CREATE OR REPLACE FUNCTION autolog_subscriptions() RETURNS void` that:
    1. Acquires `pg_try_advisory_lock(8830731)`. The integer is the reserved lock slot for this function — document the reservation in a comment in the migration body (`-- lock slot 8830731 reserved for autolog_subscriptions; see DESIGN.md §14.3`). If not acquired, return immediately (another invocation is already running).
    2. For each subscription with `status = 'active' AND next_billing_date <= current_date`:
       - `INSERT INTO transactions (user_id, card_id, subscription_id, merchant, amount, date, category, source) VALUES (..., 'auto_logged') ON CONFLICT (subscription_id, date) WHERE status = 'active' AND subscription_id IS NOT NULL DO NOTHING`. **The `WHERE` predicate on the `ON CONFLICT` clause is required** because the underlying unique index ([20260516150000_status_columns_and_soft_delete.sql:80-82](supabase/migrations/20260516150000_status_columns_and_soft_delete.sql#L80-L82)) is partial; without the matching predicate Postgres throws "no unique or exclusion constraint matching the ON CONFLICT specification."
       - `card_id` is passed through from the subscription row — `NULL` for ACH subscriptions, which the `transactions.card_id` column already allows.
       - If the insert affected a row, advance `next_billing_date` by one period using `CASE frequency WHEN 'weekly' THEN + interval '1 week' WHEN 'monthly' THEN + interval '1 month' WHEN 'quarterly' THEN + interval '3 months' WHEN 'annual' THEN + interval '1 year' END`. Advance by **one period per cron run** — if a subscription is somehow multiple periods behind (shouldn't happen given the forward-only create-time rule, but defense-in-depth), it catches up one day at a time. Spamming the dashboard with N back-dated transactions on a single cron run is worse than a multi-day catch-up.
    3. Releases the advisory lock (implicit on function return; explicit `pg_advisory_unlock` is optional).
  - **Cron scheduling is NOT in this migration** — see the "Scheduling" section below.
  - Idempotency relies on the `UNIQUE (subscription_id, date)` partial index from Day 2 (re-targeted to `status = 'active'` by the §8 status-column migration).

### Scheduling

The `cron.schedule(...)` call is **production-only**. Dev runs the function manually in tests.

- Add a separate file `supabase/seed/production_cron.sql` (or equivalent — match whatever convention the AI-call-log aggregator cron uses if one already exists):
  ```sql
  SELECT cron.schedule('autolog-subscriptions', '0 6 * * *', 'SELECT autolog_subscriptions();');
  ```
- This file is applied only in the production Supabase project, not in dev. Document the file path and apply procedure in DESIGN.md §14.3 if not already there.
- In dev and tests, callers invoke `SELECT autolog_subscriptions();` directly.

### Backend

- **New file** [app/routes/subscriptions.py](app/routes/subscriptions.py):

  - **`POST /subscriptions/confirm`** — body: `SubscriptionConfirmRequest` (`name`, `amount`, `frequency`, `start_date`, `category`, `next_billing_date`, `client_request_id: UUID`, `card_id: UUID | None`). `next_billing_date` is computed by the `propose_subscription` tool from `start_date + frequency` using the forward-only rule (see "Forward-only rule" below); `client_request_id` is minted by the same tool. Writes the row. Called after "looks right" on the chat parse card (UX frame 15 in `subscription` kind).
    - **Validate `card_id` ownership when present**: if `card_id is not None`, read `cards` via `supabase_for_user(user.jwt)` and reject with 422 if the id doesn't resolve for this user. Same rationale as Day 5's transaction confirm — the subscriptions RLS policy only enforces `user_id = auth.uid()`, not that `card_id` belongs to the authed user, so a tampered client could FK-link to another user's card id. RLS on `cards` prevents the read later, but not the write now. **Skip the check when `card_id is None`** — the column is now nullable for cardless subscriptions.
    - **Amount and frequency validation**: `amount > 0`, `frequency` in `{monthly, quarterly, annual, weekly}` (matches §8.3). 422 on miss.
    - **Category validation**: must be in `ALLOWED_CATEGORIES` ([app/prompts/categories.py](app/prompts/categories.py)). 422 on miss.
    - **Idempotent insert keyed on `client_request_id`.** Same shape as Day 5's transactions confirm: if a row already exists for `(user_id, client_request_id)`, return that row instead of inserting a duplicate. Without this, a Day 15 offline-queue drain that retries after a lost response would create a duplicate subscription — and `pg_cron` would then auto-log a duplicate transaction every billing cycle until the user notices. The cost of the duplicate is not constant (one delete, as for cards) but grows monthly until manually reconciled. See "Why subscriptions get idempotency where cards don't" above.

  - **`GET /subscriptions?status=<active|paused|cancelled|all>`** — list. Default `status=active`. Returns rows ordered by `next_billing_date ASC`. Used by `/subscriptions` page and the `get_subscriptions()` agent tool's underlying read.

  - **`PATCH /subscriptions/{id}`** — accepts `amount`, `category`, `name`, `card_id`, and `status` transitions (`active` ↔ `paused`; `active` → `cancelled` is allowed but normally goes through `DELETE`). **Rejects `frequency` and `start_date` with 422** per the §8.3 immutability rule; UI surfaces the hint "cancel and re-add to change billing cadence." When `card_id` is updated, re-run the ownership check (and accept `null` for cardless subscriptions). Used by UX frame 22's "pause subscription" / "resume subscription" / edit-amount actions and the needs-new-card reassignment banner (see "Card-soft-delete handling" below).

  - **`DELETE /subscriptions/{id}`** — soft cancel (`status = 'cancelled'`). The row stays so historical auto-logged transactions retain their `subscription_id` link (§8.3 cancel/re-add doctrine).

  - **No `POST /subscriptions` that accepts free-form user input** — adds go through chat → `propose_subscription` → `POST /subscriptions/confirm` (invariant 8).

- **Card-soft-delete handling** — atomic cascade via `soft_delete_card(p_card_id UUID)`:
  - **New migration** `..._soft_delete_card_function.sql`: a `SECURITY DEFINER` plpgsql function that runs the split-cascade and the card UPDATE in a single transaction. Three separate PostgREST UPDATEs from the route would not be atomic — a failure between passes would leave the user with AF rows cancelled, regular subs paused, and the card still in their wallet until they retried. One SQL transaction makes the operation all-or-nothing.
  - Function body, one CASE-based UPDATE on subscriptions + one UPDATE on cards:
    - **Regular subscriptions** (`name NOT LIKE '% annual fee'` etc.) → `status = 'paused'`.
    - **Card annual-fee subscriptions** (Day 19b — `name LIKE '% annual fee'` AND `category = 'Subscriptions'` AND `frequency = 'annual'`) → `status = 'cancelled'`.
    - **Card itself** → `status = 'deleted'`, `deleted_at = NOW()`.
  - Every WHERE in the function is filtered by `auth.uid()` (which PostgREST populates from the JWT). The SECURITY DEFINER posture doesn't widen the access boundary — a user can only soft-delete their own card.
  - `REVOKE EXECUTE FROM PUBLIC; GRANT EXECUTE TO authenticated;` — end-user JWTs reach the function through `client.rpc(...)` in [app/routes/cards.py](app/routes/cards.py).
  - The route shrinks to one `client.rpc("soft_delete_card", {"p_card_id": str(card_id)}).execute()` call.
  - Day 19b ships the AF-creation half; the cascade *recognises* AF rows here on Day 19 so that when 19b's dual-write lands, the soft-delete already handles them correctly. If 19b hasn't shipped yet, the AF-shape branch matches zero rows and the regular-subscription branch is the only active path.

### `propose_subscription` agent tool — [app/agent/tools.py](app/agent/tools.py)

- Shape: `propose_subscription({name, amount, frequency, start_date, category, card_id?}) → SubscriptionProposal`. **`category` is required**; `card_id` is optional. If the user hasn't named a category, Claude infers one from the merchant or asks; the tool 422s if the category is not in `ALLOWED_CATEGORIES`. If the user names a card, Claude resolves `card_name → card_id` via `get_cards(...)` before calling the tool; if no card is named ("track my rent"), the tool returns `card_id: null` in the proposal.
- **Forward-only `next_billing_date` computation:** if `start_date <= today`, set `next_billing_date = today + 1 period`; if `start_date > today`, `next_billing_date = start_date`. The proposal payload carries both `start_date` (as entered) and `next_billing_date` (as computed) so the parse card can show "next auto-log: {next_billing_date}" with the explanatory micro-text "we won't auto-log past charges — log them manually if you want them on the ledger." Matches the §8.3 forward-only rule.
- Mints a fresh `client_request_id = uuid4()` and includes it on the returned `SubscriptionProposal` (same shape as `propose_transaction`). Does not `.insert()` into `subscriptions` — the invariant-guard test from Day 9b enforces this ([tests/contracts/test_tool_write_invariant.py:37](tests/contracts/test_tool_write_invariant.py#L37) — `propose_subscription` must not be in `ALLOWED_DIRECT_WRITE_TOOLS`).
- The parse card carries `client_request_id` opaquely through "let me fix it" edits — the id identifies the user's commit intent, not the payload contents, mirroring Day 5's transaction-confirm contract.
- Add to `TOOL_REGISTRY`. Update the system prompt's tool descriptions to include `propose_subscription` (mention the cardless case explicitly: "for ACH bills like rent, the user can omit a card") and bump `PROMPT_VERSION`.

### Offline queue extension — [frontend/src/lib/offline_queue.ts](frontend/src/lib/offline_queue.ts)

- Add `"subscription"` to the `kind` union on the `pending_confirms` schema. Existing IndexedDB entries (transaction and card) remain valid; this is an additive schema change, no migration needed (IndexedDB is schemaless at the application level).
- Extend the drain switch to route `kind === "subscription"` entries to `POST /subscriptions/confirm`.
- Drain semantics for subscriptions mirror the transaction branch (Day 15): 2xx → dequeue; 5xx / network error → retain, retry on next `online` event; 422 / other 4xx → pop and re-render as a parse card. There is no subscription analog of the 409 `active_card_exists` shape because subscriptions use `client_request_id` instead of a natural key; an idempotent replay returns the existing row with 2xx, not 409.
- Update [frontend/tests/offline_queue.test.ts](frontend/tests/offline_queue.test.ts): queue a subscription confirm → simulate `online` → POST fires once → queue empties. Replay the same `client_request_id` → server returns existing row → only one subscription exists in DB.

### Frontend (UX frames 21, 22)

- [frontend/src/pages/Subscriptions.tsx](frontend/src/pages/Subscriptions.tsx): list with name, amount, next billing date, auto-logged 🔄 badge on the next-to-bill row, pause/resume button. Paused rows at reduced opacity. Cardless subscriptions display "ACH / no card" instead of a card chip. Empty state footer hint: "add a new subscription via tameru ai →".
- **Needs-new-card banner**: when any of the user's subscriptions are in `status = 'paused'` *because their backing card was soft-deleted*, surface a top-of-page banner listing affected rows with a "Pick a new card" action that opens the edit sheet pre-focused on `card_id`. Detection heuristic for v1: filter `status = 'paused'` rows whose `card_id` matches a card with `status = 'deleted'` (the cards filter join from Day 14, §6.1). No new column is required.
- [frontend/src/components/SubscriptionDetail.tsx](frontend/src/components/SubscriptionDetail.tsx) (frame 22): bottom sheet with detail fields, pause/resume secondary, edit fields (amount, category, name, card), "cancel subscription" destructive text. **Frequency and start_date render as read-only** with a small "to change cadence, cancel and re-add" hint per the §8.3 immutability rule.
- **No "Add subscription" form.** Adds are chat-only. The list page's only add affordance is the "add via tameru ai" hint, which deep-links to the chat.

### Tests

- [tests/test_autolog.py](tests/test_autolog.py) — SQL-only tests, runs against the test DB without the FastAPI client:
  - Seed a subscription with `next_billing_date = today - 1 day` (cardful). Run `SELECT autolog_subscriptions();`. Assert one transaction inserted with `source = 'auto_logged'`, `card_id` populated, `next_billing_date` advanced.
  - Same as above but with `card_id = NULL`. Assert the transaction is inserted with `card_id = NULL`.
  - Run again. Assert zero new transactions (idempotency via partial unique index).
  - Run two parallel calls in separate connections (simulate concurrent cron). Assert at most one inserts (advisory-lock works).
  - Seed a `status = 'paused'` subscription with `next_billing_date <= today`. Run the cron. Assert zero new transactions (paused rows are skipped).

- [tests/routes/test_subscriptions.py](tests/routes/test_subscriptions.py) — FastAPI route tests:
  - `POST /subscriptions/confirm` with a valid cardful proposal → row created with the supplied `client_request_id`.
  - `POST /subscriptions/confirm` with `card_id = None` (cardless ACH proposal) → row created; ownership check is skipped.
  - **Idempotency**: POST the same proposal twice with the same `client_request_id` → second call returns the original row, no duplicate inserted.
  - POST with `amount <= 0` → 422; invalid `frequency` → 422; invalid `category` → 422; another user's `card_id` → 422.
  - `PATCH /subscriptions/{id}` with `frequency` → 422 with the immutability hint; `start_date` → 422 likewise.
  - `PATCH /subscriptions/{id}` with `card_id` re-pointing to another of the user's own cards → 200; with another user's `card_id` → 422.
  - PATCH `status=paused` → `status` updated; cron skip-on-paused (already covered in `test_autolog.py`).
  - RLS: user A cannot GET / PATCH / DELETE user B's subscriptions.

- [tests/routes/test_cards.py](tests/routes/test_cards.py) — extend with the soft-delete cascade test:
  - Seed an active regular subscription on a card. `DELETE /cards/{id}`. Assert the subscription's `status` flipped to `paused`. Run `autolog_subscriptions()`. Assert no transaction inserted.
  - (Day 19b will add the AF-cascade companion test once 19b ships.)

## Deferred / out of scope

- **Chat-rehydrate annotation for confirmed subscriptions.** Day 16's `_annotate_committed_proposals` joins `transactions` and `cards` on `client_request_id` to flip parse-card state to `logged.`. Subscriptions should eventually share this join, but threading them into the rehydrate path is a separate change that touches Day 16's helper — explicitly deferred from Day 19. Until that lands, a chat session that survives a subscription confirm will leave the parse card in `pending.` state on rehydrate; the row exists in DB and works correctly, only the chat preview is stale until the next live confirm.
- **Checkbox on the transaction parse card to also-track-as-recurring.** Industry-standard pattern (Copilot Money's `R` flag works exactly this way), worth shipping as a Day-19c convenience hook. Out of v1 day-19 scope.
- **Recurring-subscription detection** ("you've spent at Spotify three months in a row — track this?") — listed in `DESIGN.md` §15 as post-Phase-1, author-driven.

## Don't

- Don't add an APScheduler / FastAPI background task. `pg_cron` only.
- Don't catch exceptions inside `autolog_subscriptions()` and silently continue — let them surface in Postgres logs.
- Don't schedule the cron job in dev. Only the production seed file calls `cron.schedule(...)`. Use a manual `SELECT autolog_subscriptions();` in tests and dev.
- Don't write to `subscriptions` from inside `propose_subscription`. The tool returns a proposal; `POST /subscriptions/confirm` commits. The invariant-guard test from Day 9b will fail if it doesn't.
- Don't register `propose_subscription` in `TOOL_REGISTRY` before `POST /subscriptions/confirm` exists. Partial tool registration produces a worse UX than no tool.
- Don't backfill historical billing cycles when a user creates a subscription with a backdated `start_date`. Forward-only is the rule (§8.3); manual transaction entry is the escape hatch.
- Don't accept PATCH updates to `frequency` or `start_date`. They are immutable (§8.3). Cancel-and-re-add is the path.
- Don't cascade-cancel regular subscriptions when a card is soft-deleted. Flip to `paused` and let the user reassign (§8.3 split-cascade). The cancel-cascade applies only to card annual-fee subscriptions (Day 19b).

## Done when

- Adding a cardful subscription with `start_date = today, frequency = monthly` produces `next_billing_date = today + 1 month` (forward-only rule applied at confirm time).
- Adding a cardful subscription with `start_date = today - 5 days, frequency = monthly` produces `next_billing_date = today + 1 month` (not `today - 5 days + 1 month` — forward-only).
- Adding a cardless subscription ("track my rent at $2400 monthly, no card") succeeds; the row has `card_id = NULL`; subsequent auto-logs produce transactions with `card_id = NULL`.
- Manually running `SELECT autolog_subscriptions();` with a subscription whose `next_billing_date = today` creates 1 transaction (`source = 'auto_logged'`); re-running creates 0.
- Two concurrent `SELECT autolog_subscriptions()` calls don't double-insert (advisory lock works).
- Auto-logged transactions show up in the transaction list with the 🔄 icon.
- Replaying the same `POST /subscriptions/confirm` payload (same `client_request_id`) returns the original row — no duplicate subscription row, and the next cron run still produces exactly 1 transaction for that billing date (not 2).
- The Day 15 offline queue, with a queued subscription confirm, drains successfully on reconnect and the user sees exactly one subscription appear in the list.
- `PATCH /subscriptions/{id}` with `{"frequency": "annual"}` returns 422 with the immutability hint; with `{"amount": 19.99}` succeeds.
- Soft-deleting a card with an active regular subscription flips that subscription's `status` to `paused`; the cron stops auto-logging; `/subscriptions` shows the "needs new card" banner; PATCHing the subscription with a new `card_id` and `status = 'active'` resumes auto-logging on the next cron run.
