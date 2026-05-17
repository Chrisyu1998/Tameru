# Day 19 — Subscription manager + `pg_cron` auto-logger with idempotency

## Goal

User adds a subscription. Daily `pg_cron` SQL function inserts a transaction whenever `next_billing_date <= today` and advances `next_billing_date`. Idempotent under retries. Survives Railway deploys (because it doesn't run in FastAPI).

This day also lands the `propose_subscription` agent tool and registers it in `TOOL_REGISTRY`. The tool registration deliberately waited until the `POST /subscriptions/confirm` endpoint exists — registering a tool whose confirm endpoint returns 404 means the user taps "looks right" on a perfect-looking parse card and gets a backend error. Tools that can't end-to-end commit are not in the registry.

## Read first

- `DESIGN.md` §6.5 (subscription auto-logger), §8.3 (subscriptions schema — note `client_request_id`), §14.3 (pg_cron rationale).
- `CLAUDE.md` invariant 4.
- Day 5's `client_request_id` idempotency pattern (`app/routes/transactions.py` confirm path + the `transactions_user_client_request_id_unique` partial index in `supabase/migrations/`). Subscriptions adopt the same shape — see "Why subscriptions get idempotency where cards don't" below.

## Why `client_request_id` on subscriptions

After the Day 15 cards follow-up (migration `20260517120000_cards_client_request_id.sql`), every ledger-adjacent table that flows through propose-then-confirm now carries a `client_request_id`. Same name across all three; **different roles depending on whether a natural uniqueness key exists**:

| Table | Natural key? | Role of `client_request_id` |
|---|---|---|
| `transactions` | None — two coffees at the same place on the same day on the same card is a normal pattern | Idempotency token + chat-rehydrate join key. Same-crid replay returns the existing row. |
| `cards` | `(user_id, issuer, last_four)` is structurally unique (one physical card per user per issuer) | Chat-rehydrate join key (disambiguates two same-name cards) + same-crid replay shortcut. The natural-key 409 still owns the "different proposals for the same physical card" case. |
| `subscriptions` | **None** — a family plan and a personal plan on the same card with the same name and frequency are both valid | Idempotency token + chat-rehydrate join key. Same shape as transactions. |

For subscriptions specifically, the Day 15 offline-queue + pg_cron-auto-logger combination amplifies the cost of a duplicate row:

- A duplicated subscription gets independently auto-logged by `pg_cron` every billing cycle (the `UNIQUE (subscription_id, date)` index is keyed on `subscription_id`, so two dup subscription rows produce two transaction rows per month).
- The recovery cost is not "one delete" (as for cards) but "delete the dup subscription + delete N already-auto-logged duplicate transactions," and N grows with months-until-the-user-notices.

So subscriptions get the full transactions-style treatment: nullable `client_request_id` column, partial unique index, route-level same-crid short-circuit. The `propose_subscription` tool mints a fresh UUID per call exactly the way `propose_card` (post-Day-15) and `propose_transaction` already do — copy that pattern from `app/agent/tools.py`.

## Deliverables

- New migration `..._subscriptions_client_request_id.sql`:
  - `ALTER TABLE subscriptions ADD COLUMN client_request_id UUID;` (nullable — pg_cron-written rows leave it NULL, and the partial index excludes them).
  - `CREATE UNIQUE INDEX subscriptions_user_client_request_id_unique ON subscriptions (user_id, client_request_id) WHERE client_request_id IS NOT NULL;`
  - Update `DESIGN.md` §8.3 in the same change to include the new column row (already done in this prompt's design-doc edits).

- New migration `..._subscription_autolog_function.sql`:
  - `CREATE OR REPLACE FUNCTION autolog_subscriptions() RETURNS void` that:
    1. Acquires `pg_try_advisory_lock(<some constant int>)`. If not acquired, returns immediately.
    2. For each subscription with `status='active' AND next_billing_date <= current_date`:
       - `INSERT INTO transactions (user_id, card_id, subscription_id, merchant, amount, date, category, source) VALUES (...) ON CONFLICT (subscription_id, date) DO NOTHING`.
       - If the insert affected a row, advance `next_billing_date` by one period (use a CASE on `frequency`).
    3. Releases the advisory lock.
  - Idempotency relies on the `UNIQUE (subscription_id, date)` constraint from Day 2.
- `SELECT cron.schedule('autolog-subscriptions', '0 6 * * *', 'SELECT autolog_subscriptions();');` — runs daily at 06:00 UTC.
- Backend:
  - `app/routes/subscriptions.py`:
    - **`POST /subscriptions/confirm`** — body: `SubscriptionConfirmRequest` (name, card_id, amount, frequency, start_date, category, next_billing_date, **`client_request_id: UUID`**). `next_billing_date` is computed by the `propose_subscription` tool from `start_date + frequency`; `client_request_id` is minted by the same tool. Writes the row. Called after "looks right" on the chat parse card (UX frame 15 in `subscription` kind).
        - **Validate `card_id` ownership** before insert: read `cards` via `supabase_for_user(user.jwt)` and reject with 422 if the id doesn't resolve for this user. Same rationale as Day 5's transaction confirm — the subscriptions RLS policy only enforces `user_id = auth.uid()`, not that `card_id` belongs to the authed user, so a tampered client could FK-link to another user's card id. RLS on `cards` prevents the read later, but not the write now.
        - **Amount and frequency validation**: `amount > 0`, `frequency` in `{monthly, quarterly, annual, weekly}` (matches §8.3). 422 on miss.
        - **Idempotent insert keyed on `client_request_id`.** Same shape as Day 5's transactions confirm: if a row already exists for `(user_id, client_request_id)`, return that row instead of inserting a duplicate. Without this, a Day 15 offline-queue drain that retries after a lost response would create a duplicate subscription — and `pg_cron` would then auto-log a duplicate transaction every billing cycle until the user notices. The cost of the duplicate is not constant (one delete, as for cards) but grows monthly until manually reconciled. See "Why subscriptions get idempotency where cards don't" above.
    - **No `POST /subscriptions` that accepts free-form user input** — adds go through chat → `propose_subscription` → `POST /subscriptions/confirm` (invariant 8).
- `propose_subscription` agent tool — `app/agent/tools.py`:
  - Shape: `propose_subscription({name, amount, frequency, start_date, category?, card_id?}) → SubscriptionProposal`.
  - Computes `next_billing_date` from `start_date + frequency`, **mints a fresh `client_request_id = uuid4()` and includes it on the returned `SubscriptionProposal`** (same shape as `propose_transaction`). Does not `.insert()` into `subscriptions` — the invariant-guard test from Day 9b enforces this (`propose_subscription` must not be in `ALLOWED_DIRECT_WRITE_TOOLS`).
  - The parse card carries `client_request_id` opaquely through "let me fix it" edits — the id identifies the user's commit intent, not the payload contents, mirroring Day 5's transaction-confirm contract.
  - Add to `TOOL_REGISTRY`. Update the system prompt's tool descriptions to include `propose_subscription` and bump `PROMPT_VERSION`.
    - `GET /subscriptions?status=` — list.
    - `PATCH /subscriptions/{id}` — pause (`status=paused`), resume (`status=active`), edit fields. Used by UX frame 22's "pause subscription" action and the edit sheet.
    - `DELETE /subscriptions/{id}` — soft cancel (`status=cancelled`).
- **Extend Day 15's offline confirm queue** — `frontend/src/lib/offline_queue.ts`:
  - Add `"subscription"` to the `kind` union on the `pending_confirms` schema. Existing IndexedDB entries (transaction and card) remain valid; this is an additive schema change, no migration needed (IndexedDB is schemaless at the application level).
  - Extend the drain switch to route `kind === "subscription"` entries to `POST /subscriptions/confirm`.
  - The drain semantics for subscriptions mirror the transaction branch (Day 15): 2xx → dequeue; 5xx / network error → retain, retry on next `online` event; 422 / other 4xx → pop and re-render as a parse card. There is no subscription analog of the 409 `active_card_exists` shape because subscriptions use `client_request_id` instead of a natural key; an idempotent replay returns the existing row with 2xx, not 409.
  - Update `frontend/tests/offline_queue.test.ts`: queue a subscription confirm → simulate `online` → POST fires once → queue empties. Replay the same `client_request_id` → server returns existing row → only one subscription exists in DB.
- Frontend (UX frames 21, 22):
  - `frontend/src/pages/Subscriptions.tsx`: list with name, amount, next billing date, auto-logged 🔄 badge on the next-to-bill row, pause/resume button. Paused rows at reduced opacity. Empty state footer hint: "add a new subscription via tameru ai →".
  - `frontend/src/components/SubscriptionDetail.tsx` (frame 22): bottom sheet with detail fields, "pause subscription" secondary, "cancel subscription" destructive text, "to edit, ask tameru ai" micro-text.
  - **No "Add subscription" form.** Adds are chat-only. The list page's only add affordance is the "add via tameru ai" hint, which deep-links to the chat.
- Tests:
  - `tests/test_autolog.py`:
    - Seed a subscription with `next_billing_date = today - 1 day`. Run `SELECT autolog_subscriptions();`. Assert one transaction inserted, `next_billing_date` advanced.
    - Run again. Assert zero new transactions (idempotency).
    - Run two parallel calls in separate connections (simulate concurrent cron). Assert at most one inserts.
  - `tests/test_subscriptions.py`:
    - `POST /subscriptions/confirm` with a valid proposal → row created with the supplied `client_request_id`.
    - **Idempotency**: POST the same proposal twice with the same `client_request_id` → second call returns the original row, no duplicate inserted, no second pg_cron auto-log fires for the next billing cycle.
    - POST with `amount <= 0` → 422; with an invalid `frequency` → 422; with another user's `card_id` → 422.
    - PATCH `status=paused` → `status` updated; pg_cron skip-on-paused (assert no transaction inserted by the next cron run).
    - RLS: user A cannot GET / PATCH / DELETE user B's subscriptions.

## Don't

- Don't add an APScheduler / FastAPI background task. `pg_cron` only.
- Don't catch exceptions inside `autolog_subscriptions()` and silently continue — let them surface in Postgres logs.
- Don't run the cron job in Phase 1 dev; only schedule it in production. Use a manual `SELECT autolog_subscriptions();` in tests/dev.
- Don't write to `subscriptions` from inside `propose_subscription`. The tool returns a proposal; `POST /subscriptions/confirm` commits. The invariant-guard test from Day 9b will fail if it doesn't.
- Don't register `propose_subscription` in `TOOL_REGISTRY` before `POST /subscriptions/confirm` exists. Partial tool registration produces a worse UX than no tool.

## Done when

- Adding a subscription with `start_date = today - 5 days, frequency = monthly` and triggering the cron creates 1 transaction (today's billing).
- Re-running the cron creates 0 transactions.
- Two concurrent `SELECT autolog_subscriptions()` calls don't double-insert (advisory lock works).
- Auto-logged transactions show up in the transaction list with the 🔄 icon.
- Replaying the same `POST /subscriptions/confirm` payload (same `client_request_id`) returns the original row — no duplicate subscription row, and the next cron run still produces exactly 1 transaction for that billing date (not 2).
- The Day 15 offline queue, with a queued subscription confirm, drains successfully on reconnect and the user sees exactly one subscription appear in the list.
