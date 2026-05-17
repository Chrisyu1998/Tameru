# Day 19b — Card annual-fee tracking via subscriptions reuse

## Goal

When a user adds a card with an annual fee, optionally capture the
**next renewal date** on the parse card. If supplied, the server
*also* creates a `subscriptions` row alongside the `cards` row — the
existing `pg_cron` auto-logger from Day 19 then auto-logs each
year's AF to the transaction ledger on the renewal anniversary.

This is the natural extension of two pieces of infrastructure that
already exist:
- `cards.annual_fee` (Day 14 — the amount).
- `subscriptions` + `pg_cron autolog_subscriptions()` (Day 19 — the
  recurring-charge auto-logger with idempotency).

**Why this ships after Day 19, not as 14c:** the dual-write requires
the subscriptions confirm endpoint and the auto-logger to be live.
Building this before Day 19 means writing parallel cron infrastructure
or stubbing the subscription insert — both are wasted work.

## Design decisions (locked in discussion before this prompt)

- **Renewal date is OPTIONAL on the parse card.** Users who don't know
  off the top of their head skip it; the card still saves. AF tracking
  is a bonus, not a gate. No subscription row is created when the
  field is left empty.
- **Default suggestion = 1 year from today.** Date picker pre-fills
  with `today + 365 days` for new cards. User can override or clear.
  This is purely a UX nudge — the server does not auto-fill.
- **AF amount changes (Chase bumped CSR $550 → $795) reuse the
  subscriptions surface.** PATCH the subscription's amount via the
  edit sheet or `propose_subscription` from chat. No special-case
  code on the cards path.
- **No statement-credit netting.** Log the gross AF as charged.
  Statement credits (CSR's $300 travel, Amex Plat's various) are out
  of scope; users mentally subtract.
- **Cards paid in points (Amex MR, Bilt).** The AF still appears as a
  cash transaction on the statement. No special case — same flow.
- **Date only, no time.** Matches `subscriptions.next_billing_date`
  type (DATE, not TIMESTAMPTZ).

## Read first

- `DESIGN.md` §6.5 (subscription auto-logger), §8.1 (cards schema —
  the `annual_fee` column exists; we're NOT adding `next_annual_fee`
  to `cards` because the data lives on `subscriptions`).
- `DESIGN.md` §8.3 (`subscriptions` schema — `card_id` FK with
  `ON DELETE CASCADE` already exists; that's the cascade we rely on).
- `CLAUDE.md` invariant 4 (pg_cron is the auto-logger; do not build
  a parallel FastAPI background task).
- Day 14 prompt (`day-14-cards-perplexity.md`) — for the propose-confirm
  card flow this extends.
- Day 19 prompt (`day-19-subscriptions-pgcron.md`) — for the
  subscriptions confirm endpoint and the cron function.

## Deliverables

### `CardProposal` + `POST /cards/confirm` — optional renewal date

- Add `next_annual_fee_date: date | None = None` to `CardProposal` in
  `app/models/cards.py`. Optional; defaults to None.
- Validate (when present): must be > today (no past renewal dates —
  pg_cron would auto-log them immediately, which is confusing UX for a
  date the user just typed).
- `POST /cards/confirm` flow (`app/routes/cards.py`):
  1. Insert the `cards` row as today.
  2. **If `next_annual_fee_date` is present AND `annual_fee` is present
     AND `annual_fee > 0`:**
     - Insert a `subscriptions` row in the same handler:
       ```python
       {
           "user_id": str(user.user_id),
           "card_id": str(new_card.id),
           "name": f"{proposal.name} annual fee",
           "amount": str(proposal.annual_fee),
           "frequency": "annual",
           "start_date": proposal.next_annual_fee_date.isoformat(),
           "next_billing_date": proposal.next_annual_fee_date.isoformat(),
           "category": "Fees",  # or whatever's in ALLOWED_CATEGORIES
           "status": "active",
       }
       ```
     - **Idempotency note:** if the user re-confirms the same card and
       a subscription with `(card_id, name)` already exists for them,
       skip the insert (read-then-write under RLS). This shouldn't
       fire in practice (cards don't have `client_request_id`
       idempotency, so re-confirm produces a 409 from the cards
       partial unique index) but is cheap defense.
  3. Return the new card. The companion subscription is not surfaced
     in the response — `GET /subscriptions` is the source of truth
     for it.

### `propose_card` agent tool — optional renewal date arg

- Add `next_annual_fee_date: string (date) | None` to the tool schema.
- System prompt update: teach Claude that if the user mentions when
  the AF hits ("renews in March," "my AF is March 15"), fill in
  `next_annual_fee_date`. Otherwise omit — do not guess.
- Bump `PROMPT_VERSION` (chat_vN → chat_vN+1).

### Frontend — parse card row

- `AddCardStep.tsx` and the chat-rendered parse card both add a single
  optional row when the lookup returns an `annual_fee`:

  ```
  annual fee:      $550
  next renewal:    [ Mar 15, 2027 ] ✕   (optional)
                   ↳ we'll auto-log it when it hits.
  ```

- Date picker default: `today + 365 days`. ✕ clears the field.
- When empty, the dual-write does not fire.
- When set, "we'll auto-log it" tooltip / sub-label tells the user
  what's about to happen.
- 409 collision flow unchanged.

### Cards-list affordance (UX surface)

- On `pages/cards.tsx` tile, render a small chip when the card has an
  associated AF subscription:
  - `🔄 $550 · next Mar 15` (uses the subscription's `next_billing_date`).
  - Tap → opens the subscription detail sheet (UX frame 22 from Day
    19) where the user can edit / pause / cancel just like any
    subscription.
- Reads from a joined `GET /cards?include_annual_fee=true` (or a
  client-side join against `GET /subscriptions` filtered by
  `card_id`). Pick the cleaner of the two — probably the client-side
  join since `GET /subscriptions` is already fetched on the
  subscriptions page.

### Deletion cascade — verify, do not add code

- `subscriptions.card_id` already has `ON DELETE CASCADE`
  (DESIGN.md §8.3). When the user soft-deletes a card via
  `DELETE /cards/:id` (sets `active=false`), the *card* row stays —
  the cascade only fires on a true SQL DELETE.
- **This is a problem.** Soft-delete means the subscription stays
  active and pg_cron keeps logging AFs against a card the user
  thinks they cancelled.
- Fix: extend `DELETE /cards/:id` to also flip any companion AF
  subscription to `status='cancelled'`:
  ```sql
  UPDATE subscriptions
     SET status = 'cancelled'
   WHERE card_id = $1
     AND status = 'active';
  ```
  RLS scopes the write to the user's own subscriptions. One extra
  statement in the soft-delete handler.
- Add an integration test: soft-delete a card with an AF subscription
  → the subscription's status flips to `cancelled` → cron no longer
  auto-logs.

### DESIGN.md update

- §8.1 cards schema commentary: note that AF tracking is implemented
  via a companion `subscriptions` row, not a column on `cards`.
- §6.5 subscriptions auto-logger: add one bullet — "card annual fees
  participate in this auto-log path; the subscription row is created
  by `POST /cards/confirm` when the user supplies a renewal date."

### Tests

- `tests/routes/test_cards.py` extensions:
  - Confirm with `annual_fee=550, next_annual_fee_date=today+30` →
    a `subscriptions` row exists with the expected fields.
  - Confirm without `next_annual_fee_date` → no subscription row.
  - Confirm with `annual_fee=0` → no subscription row (zero-fee cards
    don't need tracking even if the user types a date).
  - Soft-delete the card → companion subscription flips to
    `cancelled` (cron will no longer fire).
- `tests/test_autolog.py` extension:
  - A card AF subscription with `next_billing_date = today` auto-logs
    a transaction tagged with `category=Fees` (or whatever the chosen
    AF category is).

## Don't

- Don't add a `cards.next_annual_fee_date` column. AF data lives on
  the subscription row; coupling it to two tables creates a sync
  problem with no upside.
- Don't auto-create the subscription without an explicit user-supplied
  date. Defaulting to "today + 365 days" silently would create a
  phantom AF notification a year out from a date the user never
  confirmed.
- Don't build a separate "cards renewals" cron or table. The
  subscriptions infrastructure already solves the same shape.
- Don't try to net statement credits. Log the gross AF.
- Don't infer the renewal date from `web_search`. Per-user fact;
  the web doesn't know.
- Don't surface the companion subscription as a separate UI concept.
  It IS a subscription — let it appear on the subscriptions list and
  the cards-list AF chip; that's enough.

## Done when

- Adding a card with `annual_fee=550` and a renewal date 30 days out
  creates both a `cards` row and a companion `subscriptions` row.
- 30 days later, `autolog_subscriptions()` inserts an AF transaction
  attributed to the right card.
- Re-running the cron does not double-insert (idempotency from Day
  19's `UNIQUE (subscription_id, date)` still holds).
- Adding a card without a renewal date creates only the `cards` row.
- Soft-deleting a card flips its AF subscription to `cancelled`; the
  cron stops auto-logging.
- The cards-list tile shows a `🔄 $550 · next Mar 15` chip for cards
  with active AF tracking; tapping opens the subscription detail.
- A chat exchange "add my Amex Platinum, AF renews March 15"
  produces a parse card with the date pre-filled and the auto-log
  hint visible.
