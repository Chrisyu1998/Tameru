# Day 19 — Subscription manager + `pg_cron` auto-logger with idempotency

## Goal

User adds a subscription. Daily `pg_cron` SQL function inserts a transaction whenever `next_billing_date <= today` and advances `next_billing_date`. Idempotent under retries. Survives Railway deploys (because it doesn't run in FastAPI).

This day also lands the `propose_subscription` agent tool and registers it in `TOOL_REGISTRY`. The tool registration deliberately waited until the `POST /subscriptions/confirm` endpoint exists — registering a tool whose confirm endpoint returns 404 means the user taps "looks right" on a perfect-looking parse card and gets a backend error. Tools that can't end-to-end commit are not in the registry.

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
    - **`POST /subscriptions/confirm`** — body: `SubscriptionProposal` (name, card_id, amount, frequency, start_date, category, next_billing_date — computed from start_date + frequency by the `propose_subscription` tool, registered in this day's `TOOL_REGISTRY` update). Writes the row. Called after "looks right" on the chat parse card (UX frame 15 in `subscription` kind).
        - **Validate `card_id` ownership** before insert: read `cards` via `supabase_for_user(user.jwt)` and reject with 422 if the id doesn't resolve for this user. The same rationale as Day 5's transaction confirm — the subscriptions RLS policy only enforces `user_id = auth.uid()`, not that `card_id` belongs to the authed user, so a tampered client could FK-link to another user's card id. RLS on `cards` prevents the read later, but not the write now.
        - **Amount and frequency validation**: `amount > 0`, `frequency` in `{monthly, quarterly, annual, weekly}` (matches §8.3). 422 on miss.
        - **No `client_request_id` idempotency.** Subscriptions are low-frequency (a handful per user) — same rationale as cards (Day 14). A rare offline-replay duplicate is recoverable by the user deleting one; the server-side idempotency machinery Day 5 uses for transactions is not proportionate here. If a user reports duplicated subscriptions in practice, revisit.
    - **No `POST /subscriptions` that accepts free-form user input** — adds go through chat → `propose_subscription` → `POST /subscriptions/confirm` (invariant 8).
- `propose_subscription` agent tool — `app/agent/tools.py`:
  - Shape: `propose_subscription({name, amount, frequency, start_date, category?, card_id?}) → SubscriptionProposal`.
  - Computes `next_billing_date` from `start_date + frequency` and returns the proposal. **Does not `.insert()`** into `subscriptions` — the invariant-guard test from Day 9b enforces this (`propose_subscription` must not be in `ALLOWED_DIRECT_WRITE_TOOLS`).
  - Add to `TOOL_REGISTRY`. Update the system prompt's tool descriptions to include `propose_subscription` and bump `PROMPT_VERSION`.
  - No `client_request_id` on subscription proposals — same low-frequency rationale as `propose_card`.
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
- Don't write to `subscriptions` from inside `propose_subscription`. The tool returns a proposal; `POST /subscriptions/confirm` commits. The invariant-guard test from Day 9b will fail if it doesn't.
- Don't register `propose_subscription` in `TOOL_REGISTRY` before `POST /subscriptions/confirm` exists. Partial tool registration produces a worse UX than no tool.

## Done when

- Adding a subscription with `start_date = today - 5 days, frequency = monthly` and triggering the cron creates 1 transaction (today's billing).
- Re-running the cron creates 0 transactions.
- Two concurrent `SELECT autolog_subscriptions()` calls don't double-insert (advisory lock works).
- Auto-logged transactions show up in the transaction list with the 🔄 icon.
