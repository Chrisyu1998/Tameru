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
- **AF category is `'Subscriptions'`** (from `ALLOWED_CATEGORIES` in
  [app/prompts/categories.py](app/prompts/categories.py)). The
  taxonomy doesn't have a separate "Fees" bucket — `Subscriptions`
  is the semantically correct closed-enum value for an annually
  recurring card fee.
- **AF subscriptions are cancelled (not paused) when the card is
  soft-deleted** (DESIGN.md §8.3 split-cascade rule). The fee is
  billed *by* the card; there is no third-party recipient to
  re-point at and no scenario where pausing-then-resuming makes
  sense. Regular subscriptions follow the pause-and-reassign path
  per Day 19.
- **AF rows are hidden from the user-facing `/subscriptions` page**
  (DESIGN.md §6.5). They're conceptually a card consequence — the
  user can't reassign the AF to a different card or cancel it
  independently of the card itself. The cards-list AF chip is the
  only surface that lists them. `GET /subscriptions` defaults to
  `include_card_af=false`; the chip passes `include_card_af=true`.
  The auto-logged AF transaction still lands in the main ledger
  with the 🔄 badge — that part the user *does* want to see.

## Read first

- `DESIGN.md` §6.5 (subscription auto-logger), §8.1 (cards schema —
  the `annual_fee` column exists; we're NOT adding `next_annual_fee`
  to `cards` because the data lives on `subscriptions`).
- `DESIGN.md` §8.3 (`subscriptions` schema — `card_id` is nullable
  for the regular-subscription case but always populated for the
  AF dual-write; the split-cascade rule defines AF vs. regular
  recognition).
- `CLAUDE.md` invariant 4 (pg_cron is the auto-logger; do not build
  a parallel FastAPI background task).
- Day 14 prompt ([day-14-cards-perplexity.md](prompt/week-2-chat-mvp-and-deploy/day-14-cards-perplexity.md))
  — for the propose-confirm card flow this extends.
- Day 19 prompt ([day-19-subscriptions-pgcron.md](prompt/week-3-polish-and-extras/day-19-subscriptions-pgcron.md))
  — for the subscriptions confirm endpoint, the cron function, the
  forward-only auto-log rule, the immutability of `frequency` /
  `start_date`, and the AF-recognition heuristic the soft-delete
  cascade uses.

## Deliverables

### `CardProposal` + `POST /cards/confirm` — optional renewal date

- Add `next_annual_fee_date: date | None = None` to `CardProposal` in
  [app/models/cards.py](app/models/cards.py). Optional; defaults to None.
- Validate (when present): must be **`>= today`**. Same-day renewals are
  legitimate (the card might charge the AF today); only strictly past
  dates are rejected. Past-date rejection prevents the pg_cron auto-logger
  from immediately firing on a date the user typed by mistake. (Note: the
  Day 19 forward-only rule applies after creation — `next_billing_date` is
  clamped to `today + 1 period` if the resulting subscription's
  `start_date <= today`; the validation here is defense-in-depth.)
- `POST /cards/confirm` flow in [app/routes/cards.py](app/routes/cards.py):
  1. Insert the `cards` row as today.
  2. **If `next_annual_fee_date` is present AND `annual_fee` is present
     AND `annual_fee > 0`:**
     - Insert a `subscriptions` row in the same handler with a
       **freshly minted `client_request_id`**:
       ```python
       {
           "user_id": str(user.user_id),
           "card_id": str(new_card.id),
           "name": f"{proposal.name} annual fee",
           "amount": str(proposal.annual_fee),
           "frequency": "annual",
           "start_date": proposal.next_annual_fee_date.isoformat(),
           "next_billing_date": proposal.next_annual_fee_date.isoformat(),
           "category": "Subscriptions",
           "status": "active",
           "client_request_id": str(uuid4()),
       }
       ```
       The `client_request_id` is server-minted here (no client supplies
       it for the AF case — the user never sees a separate subscription
       parse card). Crid is still load-bearing because it makes the
       partial unique index `subscriptions_user_client_request_id_unique`
       (§8.3) a no-op on retry rather than relying on a racy
       application-layer `(card_id, name)` read-then-write. The previous
       version of this prompt skipped the crid and relied on an
       app-layer check; that was racy under concurrent confirms and
       inconsistent with the Day 19 doctrine.
     - Wrap the cards INSERT and subscriptions INSERT in a single
       transaction. If the subscription insert fails, roll back the
       card insert; the user retries the confirm. (The user-visible
       409 collision flow on cards still owns the "card already exists"
       case before either insert fires.)
  3. Return the new card. The companion subscription is not surfaced
     in the response — `GET /subscriptions` is the source of truth
     for it.

### `propose_card` agent tool — optional renewal date arg

- Add `next_annual_fee_date: string (date) | None` to the tool schema.
- System prompt update: teach Claude that if the user mentions when
  the AF hits ("renews in March," "my AF is March 15"), fill in
  `next_annual_fee_date`. Otherwise omit — do not guess. Per
  DESIGN.md §6.1's `web_search` allowlist, the AF *date* is not a
  fact the web knows (it's per-user) — only the AF amount is.
- Bump `PROMPT_VERSION` (chat_vN → chat_vN+1).

### Frontend — parse card row

- [AddCardStep.tsx](frontend/src/components/AddCardStep.tsx) and the
  chat-rendered parse card both add a single optional row when the
  lookup returns an `annual_fee`:

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

- On [pages/cards.tsx](frontend/src/pages/cards.tsx) tile, render a
  small chip when the card has an associated AF subscription:
  - `🔄 $550 · next Mar 15` (uses the subscription's `next_billing_date`).
  - Show only when the AF subscription has `status = 'active'` (a
    cancelled AF sub — e.g. after a soft-delete + restore-from-undo
    sequence — should not advertise an auto-log that won't fire).
  - Tap → opens an AF-edit sheet (NOT the generic subscription detail
    sheet — see "Don't" below). v1: bottom sheet with editable
    `amount` and `next_annual_fee_date`, plus "stop tracking this AF"
    secondary that flips the AF subscription's `status` to `cancelled`.
- Read source: the cards page fetches `GET /subscriptions?include_card_af=true`
  and client-side-joins by `card_id`. The default `/subscriptions`
  fetch (used by the page of the same name) deliberately omits AF
  rows (DESIGN.md §6.5), so the cards page is the only surface that
  asks for them. Frontend exposes this via `listSubscriptions("all",
  { includeCardAf: true })`.

### Deletion cascade — recognise AF, cancel (not pause)

The companion-subscription handling for card soft-delete is **already
specified in Day 19** as part of the split-cascade rule (DESIGN.md
§8.3). Day 19b's job here is to make sure the recognition heuristic
matches the rows this prompt creates:

- An AF subscription created by this prompt's dual-write has:
  - `name LIKE '% annual fee'`
  - `category = 'Subscriptions'`
  - `frequency = 'annual'`
- Day 19's cascade in `DELETE /cards/{id}` uses exactly that triple
  to recognise AF rows and flip them to `status = 'cancelled'`.
  Regular subscriptions (Netflix, gym) on the same card flip to
  `status = 'paused'` and surface in the "needs new card" banner.
- **No code change is needed in [app/routes/cards.py](app/routes/cards.py)'s `DELETE` handler**
  — Day 19 already shipped it. Day 19b only needs to add the
  *test* asserting the cascade behaves correctly for the AF case
  it just enabled.

### DESIGN.md updates

The §8.3 split-cascade rule and the §6.5 "card annual fees
participate in this auto-log path" bullet are already in place
(folded in during the Day 19 design pass). Day 19b additionally:

- §8.1 cards schema commentary: note that AF tracking is implemented
  via a companion `subscriptions` row, not a column on `cards`, and
  that the soft-delete cascade for AF subscriptions is "cancelled,
  not paused" per the §8.3 split-cascade rule.

### Tests

- [tests/routes/test_cards.py](tests/routes/test_cards.py) extensions:
  - Confirm with `annual_fee=550, next_annual_fee_date=today+30` →
    a `subscriptions` row exists with `category='Subscriptions'`,
    `frequency='annual'`, `name='{card_name} annual fee'`,
    `client_request_id IS NOT NULL`.
  - Confirm without `next_annual_fee_date` → no subscription row.
  - Confirm with `annual_fee=0` → no subscription row (zero-fee cards
    don't need tracking even if the user types a date).
  - Confirm with `next_annual_fee_date` in the past → 422.
  - **Soft-delete the card → companion AF subscription's `status` flips
    to `'cancelled'`** (not paused; per the split-cascade rule). A
    co-located regular subscription on the same card flips to
    `'paused'`. Run `autolog_subscriptions()`; assert no transactions
    fire for either.
  - Same-`client_request_id` retry of the `POST /cards/confirm` (e.g.
    network blip): the cards 409 short-circuits before the subscription
    insert runs, so no duplicate AF subscription is created.

- [tests/test_autolog.py](tests/test_autolog.py) extension:
  - A card AF subscription with `next_billing_date = today` auto-logs
    a transaction with `category = 'Subscriptions'`, `source = 'auto_logged'`,
    `card_id` set to the original card id, and the right amount.

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
- Don't surface AF rows on the user-facing `/subscriptions` page.
  They're hidden by default (`GET /subscriptions` defaults to
  `include_card_af=false`); only the cards-list AF chip and an
  AF-edit sheet reached from the cards page should display them.
  Listing them next to Netflix and rent on `/subscriptions`
  conflates two different concepts — Netflix is a thing the user
  can cancel or move to a new card; an AF is bound to its card and
  is cancelled only by deleting the card itself. Same reason the
  generic subscription detail sheet's "pause" affordance doesn't
  apply to AFs.
- Don't skip the `client_request_id` on the AF subscription insert.
  Without it, a retry under concurrency falls back to a racy
  app-layer check and can produce duplicates that pg_cron then
  auto-logs each year. Crid is the structural guard.
- Don't use the category string `"Fees"` for the AF subscription.
  That value is not in `ALLOWED_CATEGORIES` and the confirm path
  will 422. Use `"Subscriptions"`.
- Don't flip AF subscriptions to `'paused'` on card soft-delete.
  The fee is bound to the card; there is no reassignment path.
  Regular subscriptions follow the paused-and-reassign path
  per Day 19. AF subscriptions are cancelled.

## Done when

- Adding a card with `annual_fee=550` and a renewal date 30 days out
  creates both a `cards` row and a companion `subscriptions` row
  with `category='Subscriptions'`, `frequency='annual'`, a fresh
  `client_request_id`, and a name matching `'{card_name} annual fee'`.
- 30 days later, `autolog_subscriptions()` inserts an AF transaction
  attributed to the right card with `source='auto_logged'`.
- Re-running the cron does not double-insert (idempotency from Day
  19's partial unique index on `(subscription_id, date)` still holds).
- Adding a card without a renewal date creates only the `cards` row.
- Soft-deleting a card flips its AF subscription's `status` to
  `'cancelled'` and any co-located regular subscriptions to
  `'paused'`; the cron stops auto-logging for both; the
  needs-new-card banner surfaces the regular ones but not the AF.
- The cards-list tile shows a `🔄 $550 · next Mar 15` chip for cards
  with an active AF subscription; tapping opens the subscription
  detail.
- A chat exchange "add my Amex Platinum, AF renews March 15"
  produces a parse card with the date pre-filled and the auto-log
  hint visible.
