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
    - `POST /subscriptions` (name, card_id, amount, frequency, start_date, category) — also computes initial `next_billing_date` from start_date.
    - `GET /subscriptions?status=` — list.
    - `PATCH /subscriptions/{id}` — pause (`status=paused`), resume (`status=active`), edit fields.
    - `DELETE /subscriptions/{id}` — soft cancel (`status=cancelled`).
- Frontend:
  - `frontend/src/pages/Subscriptions.tsx`: list with name, amount, next billing date, pause/resume button.
  - Add subscription form. The "Add via chat" path is built Day 18 (chat).
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
