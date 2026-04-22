# Day 14 — Subscription manager + `pg_cron` auto-logger with idempotency

## Goal

User adds a subscription. Daily `pg_cron` SQL function inserts a transaction whenever `next_billing_date <= today` and advances `next_billing_date`. Idempotent under retries. Survives Railway deploys (because it doesn't run in FastAPI).

## Read first

- `DESIGN.md` §6.5 (subscription auto-logger), §14.3 (pg_cron rationale).
- `CLAUDE.md` invariant 4.

## Deliverables

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
    - **`POST /subscriptions/confirm`** — body: `SubscriptionProposal` (name, card_id, amount, frequency, start_date, category, next_billing_date — computed from start_date + frequency by the `propose_subscription` tool in Day 16). Writes the row. Called after "looks right" on the chat parse card (UX frame 15 in `subscription` kind).
    - **No `POST /subscriptions` that accepts free-form user input** — adds go through chat → `propose_subscription` → `POST /subscriptions/confirm` (invariant 8).
    - `GET /subscriptions?status=` — list.
    - `PATCH /subscriptions/{id}` — pause (`status=paused`), resume (`status=active`), edit fields. Used by UX frame 22's "pause subscription" action and the edit sheet.
    - `DELETE /subscriptions/{id}` — soft cancel (`status=cancelled`).
- Frontend (UX frames 21, 22):
  - `frontend/src/pages/Subscriptions.tsx`: list with name, amount, next billing date, auto-logged 🔄 badge on the next-to-bill row, pause/resume button. Paused rows at reduced opacity. Empty state footer hint: "add a new subscription via tameru ai →".
  - `frontend/src/components/SubscriptionDetail.tsx` (frame 22): bottom sheet with detail fields, "pause subscription" secondary, "cancel subscription" destructive text, "to edit, ask tameru ai" micro-text.
  - **No "Add subscription" form.** Adds are chat-only. The list page's only add affordance is the "add via tameru ai" hint, which deep-links to the chat.
- Tests:
  - `tests/test_autolog.py`:
    - Seed a subscription with `next_billing_date = today - 1 day`. Run `SELECT autolog_subscriptions();`. Assert one transaction inserted, `next_billing_date` advanced.
    - Run again. Assert zero new transactions (idempotency).
    - Run two parallel calls in separate connections (simulate concurrent cron). Assert at most one inserts.

## Don't

- Don't add an APScheduler / FastAPI background task. `pg_cron` only.
- Don't catch exceptions inside `autolog_subscriptions()` and silently continue — let them surface in Postgres logs.
- Don't run the cron job in Phase 1 dev; only schedule it in production. Use a manual `SELECT autolog_subscriptions();` in tests/dev.

## Done when

- Adding a subscription with `start_date = today - 5 days, frequency = monthly` and triggering the cron creates 1 transaction (today's billing).
- Re-running the cron creates 0 transactions.
- Two concurrent `SELECT autolog_subscriptions()` calls don't double-insert (advisory lock works).
- Auto-logged transactions show up in the transaction list with the 🔄 icon.
