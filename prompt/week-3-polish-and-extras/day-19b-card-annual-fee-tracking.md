# Day 19b — Card annual-fee tracking via subscriptions reuse

## Goal

When a user adds a card with an annual fee, optionally capture the
**next renewal date** on the parse card. If supplied, the server
*also* creates a `subscriptions` row alongside the `cards` row — the
existing `pg_cron` auto-logger from Day 19 then auto-logs each
year's AF to the transaction ledger on the renewal anniversary. Future
edits to the card's annual fee or renewal date go through the cards
surface (`PATCH /cards/{id}`) and cascade to the companion
subscription so the auto-log stays in sync.

This is the natural extension of two pieces of infrastructure that
already exist:
- `cards.annual_fee` (Day 14 — the amount; canonical for the live AF).
- `subscriptions` + `pg_cron autolog_subscriptions()` (Day 19 — the
  recurring-charge auto-logger with idempotency).

Both the create and the edit paths write to two tables and so go
through SECURITY DEFINER RPCs (same pattern as Day 19's
`soft_delete_card`) for atomicity — no best-effort double-write from
the Python route.

**Why this ships after Day 19, not as 14c:** the dual-write requires
the subscriptions confirm endpoint, the auto-logger, and the
`soft_delete_card` RPC pattern to be live. Building this before
Day 19 means writing parallel cron infrastructure or stubbing the
subscription insert — both are wasted work.

## Design decisions (locked in discussion before this prompt)

- **Renewal date is OPTIONAL on the parse card.** Users who don't know
  off the top of their head skip it; the card still saves. AF tracking
  is a bonus, not a gate. No subscription row is created when the
  field is left empty.
- **Default suggestion = 1 year from today.** Date picker pre-fills
  with `today + 365 days` for new cards. User can override or clear.
  This is purely a UX nudge — the server does not auto-fill.
- **`cards.annual_fee` is the canonical source for the live AF amount;
  the companion subscription's `amount` mirrors it via server cascade.**
  When `PATCH /cards/{id}` updates `annual_fee` (e.g. Chase bumps CSR
  $550 → $795), the route ALSO updates the active AF subscription's
  `amount` in the same call so the next pg_cron auto-log charges the
  new value. The user mental model is "I edited the card's AF" — they
  don't think about the underlying subscription row. The renewal date
  has no `cards` column (by design — see "Don't" §1), so a virtual
  `next_annual_fee_date` field on `CardPatchRequest` writes through to
  `subscriptions.next_billing_date`. Both cascades run in a SECURITY
  DEFINER RPC for atomicity — same pattern as Day 19's
  `soft_delete_card`.
- **AF dual-write at confirm time is atomic** — `POST /cards/confirm`
  calls a `insert_card_with_af(...)` SECURITY DEFINER RPC that inserts
  both rows in one SQL transaction. If either fails, neither commits;
  the route returns the same 409 / 422 the existing single-insert path
  returns. No best-effort swallow. Matches the Day 19 cascade RPC
  pattern (`soft_delete_card`, migration `20260518130300`).
- **No statement-credit netting.** Log the gross AF as charged.
  Statement credits (CSR's $300 travel, Amex Plat's various) are out
  of scope; users mentally subtract.
- **Cards paid in points (Amex MR, Bilt).** The AF still appears as a
  cash transaction on the statement. No special case — same flow.
- **Date only, no time.** Matches `subscriptions.next_billing_date`
  type (DATE, not TIMESTAMPTZ).
- **AF category is `'Memberships'`** (from `ALLOWED_CATEGORIES` in
  [app/prompts/categories.py](app/prompts/categories.py)). The
  taxonomy doesn't have a separate "Fees" bucket — `Memberships`
  is the semantically correct closed-enum value for an annually
  recurring card fee. (The bucket was renamed from `Subscriptions` in
  migration `20260519120000` per DESIGN.md §6.5 — the recognition
  triple uses `'Memberships'` everywhere it appears.)
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
- **Edit/stop-tracking goes through the cards surface, not the
  subscriptions surface.** A dedicated AF-edit bottom sheet
  (`EditCardAfSheet`) reached from the cards-list chip posts to
  `PATCH /cards/{id}` only — `annual_fee`, `next_annual_fee_date`, or
  `next_annual_fee_date: null` to stop tracking. The frontend never
  hits `PATCH /subscriptions/{id}` for an AF row. This keeps the
  EditSubscriptionSheet's pause/cancel/card-reassign affordances
  (which don't apply to AFs) out of an AF-shaped flow and reinforces
  the "AF is a card consequence" mental model.

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
  — for the propose-confirm card flow this extends. (Filename still
  says "perplexity"; the actual vendor is Claude `web_search` per
  CLAUDE.md "Model usage by task" — content is current, filename is
  legacy.)
- Day 19 prompt ([day-19-subscriptions-pgcron.md](prompt/week-3-polish-and-extras/day-19-subscriptions-pgcron.md))
  — for the subscriptions confirm endpoint, the cron function, the
  forward-only auto-log rule, the immutability of `frequency` /
  `start_date`, and the AF-recognition heuristic the soft-delete
  cascade uses. Day 19's `soft_delete_card` RPC pattern is the
  template Day 19b's two new RPCs follow.
- [supabase/migrations/20260518130300_soft_delete_card_function.sql](supabase/migrations/20260518130300_soft_delete_card_function.sql)
  — read the SECURITY DEFINER + `auth.uid()` filter shape before
  writing the new RPCs below.
- [supabase/migrations/20260519120000_rename_subscriptions_to_memberships.sql](supabase/migrations/20260519120000_rename_subscriptions_to_memberships.sql)
  — confirms `'Memberships'` is the live category literal.

## Deliverables

### Migrations — two new SECURITY DEFINER RPCs

The Supabase Python client has no transaction primitive across two
PostgREST writes. Day 19 solved the same problem for card soft-delete
with a SECURITY DEFINER plpgsql function
([20260518130300_soft_delete_card_function.sql](supabase/migrations/20260518130300_soft_delete_card_function.sql)).
Day 19b adds two more functions of the same shape so both the AF
create path and the AF edit path are atomic.

- **New migration** `..._insert_card_with_af_function.sql`:
  `insert_card_with_af(p_card jsonb, p_af jsonb) RETURNS cards`. Body:
  1. `INSERT INTO cards (...) SELECT (p_card ->> ...) ...` filtered by
     `user_id = auth.uid()`. Returns the inserted row via `RETURNING`
     into a local var.
  2. If `p_af IS NOT NULL`: `INSERT INTO subscriptions (user_id,
     card_id, name, amount, frequency, start_date, next_billing_date,
     category, status, client_request_id)` with `card_id` set to the
     just-inserted card's id, `category = 'Memberships'`, `frequency =
     'annual'`, `status = 'active'`, `name = (p_card ->> 'name') || '
     annual fee'`, `client_request_id = gen_random_uuid()` (server-
     minted; the user never sees a separate subscription parse card so
     no client-supplied crid exists for the AF case).
  3. `RETURN` the cards row.

  Failure of either insert raises within the function; the implicit
  transaction rolls back both. The user-visible 409 (natural-key
  collision on `cards_active_identity_uniq`) and 422 (validation)
  surface to the route layer the same way they do today.

  `SECURITY DEFINER`; `REVOKE EXECUTE FROM PUBLIC`; `GRANT EXECUTE TO
  authenticated`. Every WHERE clause inside filters by `auth.uid()` so
  the definer posture doesn't widen access. Mirrors the security model
  documented in `20260518130300_soft_delete_card_function.sql`'s
  header comment.

  Rationale for jsonb args (vs. one positional arg per column): the
  cards row has ~10 fields (multipliers JSONB, source_urls text[],
  optional color, optional last_four, etc.) — a positional signature
  would be brittle as schema evolves. jsonb keeps the call site
  legible and lets the route forward the validated Pydantic model
  directly via `.model_dump()`.

- **New migration** `..._update_card_af_function.sql`:
  `update_card_af(p_card_id uuid, p_annual_fee numeric, p_set_annual_fee bool, p_next_annual_fee_date date, p_set_next_date bool) RETURNS cards`.
  The two `p_set_*` booleans distinguish "field omitted" from "field
  explicitly cleared to null" — JSONB would also work but the patch
  payload is two fields, not ten, so positional is fine. Body:
  1. If `p_set_annual_fee`: `UPDATE cards SET annual_fee = p_annual_fee
     WHERE id = p_card_id AND user_id = auth.uid() AND status =
     'active'`.
  2. If an active AF subscription exists for this card (recognition
     triple: `name LIKE '% annual fee' AND category = 'Memberships'
     AND frequency = 'annual' AND status = 'active'`):
     - If `p_set_annual_fee`: `UPDATE subscriptions SET amount =
       p_annual_fee WHERE ...` so the next pg_cron auto-log charges
       the new amount.
     - If `p_set_next_date AND p_next_annual_fee_date IS NOT NULL`:
       `UPDATE subscriptions SET next_billing_date =
       p_next_annual_fee_date WHERE ...`. (`start_date` stays
       immutable per §8.3.)
     - If `p_set_next_date AND p_next_annual_fee_date IS NULL`:
       `UPDATE subscriptions SET status = 'cancelled' WHERE ...`. Stop
       tracking. The cards row stays; `cards.annual_fee` retains its
       last value as a snapshot. Re-enabling later goes through case 3.
  3. If no active AF subscription exists AND `p_set_next_date AND
     p_next_annual_fee_date IS NOT NULL` AND the post-update
     `annual_fee > 0`: `INSERT INTO subscriptions (...)` with the same
     shape `insert_card_with_af` uses. Lets the user re-enable AF
     tracking on a card that previously had no date or a cancelled AF
     subscription.
  4. `RETURN` the updated cards row.

  Same SECURITY DEFINER + `auth.uid()` filter + grant shape as the
  insert RPC.

### `CardProposal` + `POST /cards/confirm` — optional renewal date

- `next_annual_fee_date: date | None = None` already exists on
  `CardProposal` in [app/models/cards.py](app/models/cards.py) and is
  validated to `>= today` ([cards.py:209-221](app/models/cards.py#L209-L221)).
  Same-day renewals are legitimate (the card might charge the AF
  today); only strictly past dates are rejected. The Day 19 forward-
  only rule applies *only* to `propose_subscription`-driven inserts —
  the AF dual-write bypasses that path because the user has explicitly
  named the date, and rejecting it would force a confusing "we moved
  your AF to next year" UX. Keep the existing validator.
- `POST /cards/confirm` flow in [app/routes/cards.py](app/routes/cards.py):
  1. Same crid short-circuit and `last_four`-required guard as today.
  2. Build `p_card` jsonb from the validated `CardConfirmRequest`.
  3. If `next_annual_fee_date is not None AND annual_fee is not None
     AND annual_fee > 0`: build `p_af` jsonb (currently just
     `{"next_annual_fee_date": ...}` — the function reads
     `name`/`amount` from `p_card`). Else `p_af = NULL`.
  4. `client.rpc("insert_card_with_af", {"p_card": p_card, "p_af":
     p_af}).execute()`. The function returns the cards row; the route
     parses it as `CardRow` and returns it.
  5. The unique-violation taxonomy (cards_active_identity_uniq →
     409 `active_card_exists`, cards_active_client_request_id_unique →
     replay lookup) wraps the RPC call the same way it wraps the
     direct INSERT today — PostgREST surfaces the same error strings
     through `client.rpc`.
- Replace the existing `_insert_af_subscription` best-effort helper —
  delete it. The atomic RPC is the only AF-create path.

### `PATCH /cards/{id}` — AF cascade

- Add `next_annual_fee_date: date | None = None` to `CardPatchRequest`
  in [app/models/cards.py](app/models/cards.py). Apply the same
  `>= today` validator as `CardProposal`. Explicit `null` is legal and
  means "stop tracking the AF" (cancels the companion subscription).
- Route flow in [app/routes/cards.py](app/routes/cards.py)'s
  `patch_card`:
  - If neither `annual_fee` nor `next_annual_fee_date` is in
    `provided`, the route uses the existing direct-PostgREST UPDATE
    path (no RPC needed — no cascade work to do).
  - If either is in `provided`, route through `update_card_af` RPC:
    pass `p_annual_fee` (+ `p_set_annual_fee=true`) iff `annual_fee`
    was in `provided`; same for `p_next_annual_fee_date` /
    `p_set_next_date`. Other fields in the same PATCH (`name`,
    `program`, `multipliers`, `color`) are applied to the cards row
    via a follow-up direct UPDATE before returning — they don't touch
    the subscription, so atomicity with the cascade isn't required.
    (If a future patch shape mixes AF fields with multiplier edits in
    a way where atomicity matters, the RPC's signature can be widened;
    not v1.)
- 422 on `next_annual_fee_date < today` (validator), and on
  `next_annual_fee_date is not None AND annual_fee == 0 (post-patch)`
  — can't track AF on a no-fee card.

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

### Cards-list affordance — dedicated AF-edit sheet

- On [pages/cards.tsx](frontend/src/pages/cards.tsx) tile, render a
  small chip when the card has an active companion AF subscription:
  - `🔄 $550 · next Mar 15` — amount from the chip's read source
    (see below), next-renewal from `next_billing_date`. The "next"
    date is always *future* (`autolog_subscriptions()` advances
    `next_billing_date` by one period on log), so the chip shows when
    Tameru will auto-log next, not when it last logged.
  - Show only when the AF subscription has `status = 'active'`. A
    cancelled AF sub (e.g. user tapped "stop tracking" or soft-deleted
    + restored the card) should not advertise an auto-log that won't
    fire.
  - Tap → opens `EditCardAfSheet`. **Not** the generic
    `EditSubscriptionSheet` — see "Don't" §6 below for the rationale.
- **New component** [frontend/src/components/EditCardAfSheet.tsx](frontend/src/components/EditCardAfSheet.tsx):
  - Bottom sheet, two editable fields:
    - **amount** — number input. Save → `PATCH /cards/{card_id}` with
      `{ annual_fee: newValue }`. The route's cascade updates both
      `cards.annual_fee` and the AF subscription's `amount` atomically
      via `update_card_af`.
    - **next renewal** — date picker, pre-fills from the AF
      subscription's `next_billing_date`. Save → `PATCH /cards/{card_id}`
      with `{ next_annual_fee_date: newDate }`.
  - Secondary: **"stop tracking this AF"** destructive text. Sends
    `PATCH /cards/{card_id}` with `{ next_annual_fee_date: null }`.
    The RPC flips the AF subscription's `status` to `'cancelled'`;
    `cards.annual_fee` stays as the at-edit-time snapshot. To re-
    enable, the user opens the sheet on a card with no chip (a no-
    chip-rendered card with `annual_fee > 0` gets a small "track AF"
    affordance — fold this in or defer; v1 minimum is the cancel side).
  - All three actions hit one endpoint (`PATCH /cards/{id}`). The
    frontend never calls `PATCH /subscriptions/{id}` for an AF row.
- Read source for the chip + sheet: `GET /subscriptions?include_card_af=true`
  + client-side-join by `card_id`. The default `/subscriptions` fetch
  (used by the `/subscriptions` page) deliberately omits AF rows
  (DESIGN.md §6.5), so the cards page is the only surface that asks
  for them. Frontend exposes this via `listSubscriptions("all",
  { includeCardAf: true })` (already implemented per
  [subscriptions.py:107-153](app/routes/subscriptions.py#L107-L153)).

### Deletion cascade — recognise AF, cancel (not pause)

The companion-subscription handling for card soft-delete is **already
specified in Day 19** as part of the split-cascade rule (DESIGN.md
§8.3) and shipped in `soft_delete_card` (migration
`20260518130300`). Day 19b's job is to make sure the recognition
heuristic matches the rows this prompt creates and to add the test
that Day 19 deferred:

- An AF subscription created by `insert_card_with_af` has:
  - `name LIKE '% annual fee'`
  - `category = 'Memberships'`
  - `frequency = 'annual'`
- `soft_delete_card`'s CASE-based UPDATE uses exactly that triple to
  recognise AF rows and flip them to `status = 'cancelled'`. Regular
  subscriptions on the same card flip to `status = 'paused'` and
  surface in the needs-new-card banner.
- The recognition triple in
  [20260518130300_soft_delete_card_function.sql:52-56](supabase/migrations/20260518130300_soft_delete_card_function.sql#L52-L56)
  currently reads `category = 'Subscriptions'` (the file was written
  before the §6.5 rename). The CREATE OR REPLACE in
  `20260519120000_rename_subscriptions_to_memberships.sql` updated it
  to `'Memberships'`. Day 19b's new RPCs MUST use `'Memberships'`; if
  the recognition triple drifts across the three sites (insert RPC,
  update RPC, soft-delete RPC) the cascade silently misses AFs.
- **No code change is needed in `app/routes/cards.py`'s `DELETE`
  handler** — Day 19 already shipped the route shrink to one
  `client.rpc("soft_delete_card", ...)` call. Day 19b only adds the
  *test* asserting the cascade behaves correctly for AF rows it just
  enabled (deferred from Day 19 per
  [day-19-subscriptions-pgcron.md:146](prompt/week-3-polish-and-extras/day-19-subscriptions-pgcron.md#L146)).

### DESIGN.md updates

The §8.3 split-cascade rule and the §6.5 "card annual fees
participate in this auto-log path" bullet are already in place
(folded in during the Day 19 design pass). Day 19b additionally:

- §6.5: extend the "Editing the AF amount or renewal date goes through
  the cards surface" sentence to specify the cascade contract — the
  AF subscription's `amount` mirrors `cards.annual_fee`; the AF
  subscription's `next_billing_date` is updated via a virtual
  `next_annual_fee_date` field on `PATCH /cards/{id}`. Atomicity via
  `update_card_af` RPC.
- §8.1 cards schema commentary: note that the AF create and AF edit
  paths both flow through SECURITY DEFINER RPCs
  (`insert_card_with_af`, `update_card_af`), same pattern as
  `soft_delete_card`. The cards.annual_fee column is the canonical
  source for the live amount; the companion subscription's `amount`
  mirrors it via the update RPC.

### Tests

- [tests/routes/test_cards.py](tests/routes/test_cards.py) extensions:
  - **Confirm dual-write (atomic via `insert_card_with_af`):**
    - `annual_fee=550, next_annual_fee_date=today+30` → both a `cards`
      row AND a `subscriptions` row exist; the sub has
      `category='Memberships'`, `frequency='annual'`,
      `name='{card_name} annual fee'`,
      `client_request_id IS NOT NULL`, `status='active'`.
    - Confirm without `next_annual_fee_date` → cards row exists, no
      subscription row.
    - Confirm with `annual_fee=0` (and any date) → cards row exists,
      no subscription row.
    - Confirm with `next_annual_fee_date` in the past → 422 (validator),
      neither row inserted.
    - **Atomicity**: simulate a forced failure on the subscription
      insert inside the RPC (e.g. by temporarily violating a check
      constraint on the subscription side via a test fixture) →
      assert neither the card nor the subscription row exists. The
      key property is "no orphan card with a missing AF sub."
    - Same-`client_request_id` retry of `POST /cards/confirm`: the
      cards crid short-circuit returns the existing card before the
      RPC fires; no duplicate AF subscription is created.
  - **PATCH cascade via `update_card_af`:**
    - PATCH `{annual_fee: 795}` on a card with an active AF sub →
      both `cards.annual_fee` and `subscriptions.amount` are 795.
    - PATCH `{next_annual_fee_date: today+60}` on a card with an
      active AF sub → `subscriptions.next_billing_date` is today+60;
      `cards.annual_fee` unchanged; `subscriptions.start_date`
      unchanged (immutable per §8.3).
    - PATCH `{next_annual_fee_date: null}` on a card with an active
      AF sub → the AF sub's `status` flips to `'cancelled'`;
      `cards.annual_fee` unchanged (snapshot).
    - PATCH `{next_annual_fee_date: today+30}` on a card with
      `annual_fee > 0` and no active AF sub → a new AF subscription
      is inserted (re-enable path).
    - PATCH `{next_annual_fee_date: today+30}` on a card with
      `annual_fee = 0` → 422 (can't track AF on a no-fee card).
    - PATCH `{annual_fee: 550}` on a card with no active AF sub →
      `cards.annual_fee` updated; no subscription side-effect.
    - PATCH mixing AF and non-AF fields (`{annual_fee: 550, name:
      "CSR"}`) → both apply; the non-AF field uses the route-level
      UPDATE after the RPC.
    - RLS: user A PATCHing user B's card returns 404 (RPC's
      `auth.uid()` filter matches zero rows).
  - **Soft-delete cascade (Day-19-deferred AF case):**
    - Seed a card with an active AF sub AND a regular co-located sub
      (Netflix). `DELETE /cards/{card_id}`. Assert the AF sub flipped
      to `'cancelled'`, the regular sub flipped to `'paused'`, the
      card to `'deleted'`. Run `autolog_subscriptions()`; assert
      zero new transactions fire for either.

- [tests/test_autolog.py](tests/test_autolog.py) extension:
  - A card AF subscription with `next_billing_date = today` auto-logs
    a transaction with `category = 'Memberships'`,
    `source = 'auto_logged'`, `card_id` set to the original card id,
    and the right amount.
  - After a PATCH that changed `cards.annual_fee` from 550 → 795 the
    day before, the next `autolog_subscriptions()` run logs a
    transaction with `amount = 795`.

## Don't

- Don't add a `cards.next_annual_fee_date` column. The renewal date
  lives on the subscription row; a `cards` column would create a
  two-place sync. (The *amount* is a different story — `cards.annual_fee`
  already exists from Day 14 and is the canonical source; the
  subscription's `amount` is the mirror. The `update_card_af` RPC
  keeps them aligned atomically.)
- Don't write to two tables from a Python route without an RPC.
  Supabase Python has no multi-statement transaction primitive, so a
  pair of `client.table(...).insert()` / `.update()` calls is best-
  effort, not atomic. Day 19's `soft_delete_card` is the precedent;
  Day 19b adds `insert_card_with_af` and `update_card_af` for the
  same reason. If you find yourself reaching for a try/except wrap
  around a second PostgREST call, stop — write an RPC.
- Don't let the AF subscription's `amount` drift from
  `cards.annual_fee` on any write path. The cron reads
  `subscriptions.amount` for auto-logging; the chip reads
  `cards.annual_fee` (or the sub's amount — they must match) for
  display. If they desynchronize, the user sees one number on the
  card and a different number on the auto-logged transaction.
- Don't auto-create the subscription without an explicit user-supplied
  date. Defaulting to "today + 365 days" silently would create a
  phantom AF notification a year out from a date the user never
  confirmed.
- Don't build a separate "cards renewals" cron or table. The
  subscriptions infrastructure already solves the same shape.
- Don't try to net statement credits. Log the gross AF.
- Don't infer the renewal date from `web_search`. Per-user fact;
  the web doesn't know.
- Don't route the AF-edit sheet through `PATCH /subscriptions/{id}`.
  The frontend never hits the subscriptions surface for an AF row;
  all edits go through `PATCH /cards/{id}` so the cascade is the
  single code path. This also keeps `EditSubscriptionSheet`'s
  pause/cancel/card-reassign affordances (which don't apply to AFs)
  out of an AF-shaped flow.
- Don't surface AF rows on the user-facing `/subscriptions` page.
  They're hidden by default (`GET /subscriptions` defaults to
  `include_card_af=false`); only the cards-list AF chip and the
  `EditCardAfSheet` reached from it should display them. Listing
  them next to Netflix and rent on `/subscriptions` conflates two
  different concepts — Netflix is a thing the user can cancel or
  move to a new card; an AF is bound to its card and is cancelled
  only by clearing the renewal date or deleting the card itself.
- Don't skip the server-minted `client_request_id` on the AF
  subscription insert (both inside `insert_card_with_af` and the
  re-enable branch of `update_card_af`). Without it, the partial
  unique index `subscriptions_user_client_request_id_unique` (§8.3)
  has nothing to guard against under a retry race.
- Don't use the category string `"Fees"` or `"Subscriptions"` for the
  AF subscription. `"Fees"` is not in `ALLOWED_CATEGORIES` (422).
  `"Subscriptions"` was the literal before the §6.5 rename and now
  fails the recognition triple in `soft_delete_card` and the §6.5
  hide-AF filter. Use `"Memberships"` — the post-rename canonical.
- Don't flip AF subscriptions to `'paused'` on card soft-delete.
  The fee is bound to the card; there is no reassignment path.
  Regular subscriptions follow the paused-and-reassign path
  per Day 19. AF subscriptions are cancelled.

## Done when

- Adding a card with `annual_fee=550` and a renewal date 30 days out
  via `POST /cards/confirm` creates both a `cards` row and a companion
  `subscriptions` row in one SQL transaction (via
  `insert_card_with_af`) with `category='Memberships'`,
  `frequency='annual'`, a fresh server-minted `client_request_id`, and
  a name matching `'{card_name} annual fee'`.
- A forced failure on the subscription insert inside the RPC leaves
  the cards row uninserted as well — no orphan card.
- 30 days later, `autolog_subscriptions()` inserts an AF transaction
  attributed to the right card with `source='auto_logged'` and
  `amount` equal to the *current* `cards.annual_fee` (i.e., reflects
  any intervening PATCH).
- Re-running the cron does not double-insert (idempotency from Day
  19's partial unique index on `(subscription_id, date)` still holds).
- Adding a card without a renewal date creates only the `cards` row.
- `PATCH /cards/{card_id}` with `{annual_fee: 795}` on a card with an
  active AF sub updates BOTH `cards.annual_fee` AND
  `subscriptions.amount` to 795 atomically via `update_card_af`. The
  next pg_cron auto-log charges 795, not the old amount.
- `PATCH /cards/{card_id}` with `{next_annual_fee_date: <date>}`
  updates the AF sub's `next_billing_date`; with
  `{next_annual_fee_date: null}` cancels the AF sub.
- `PATCH /cards/{card_id}` with `{next_annual_fee_date: <date>}` on a
  card whose AF sub was previously cancelled (and `annual_fee > 0`)
  re-enables tracking by inserting a fresh AF subscription.
- Soft-deleting a card flips its AF subscription's `status` to
  `'cancelled'` and any co-located regular subscriptions to
  `'paused'`; the cron stops auto-logging for both; the
  needs-new-card banner surfaces the regular ones but not the AF.
- The cards-list tile shows a `🔄 $550 · next Mar 15` chip for cards
  with an active AF subscription; tapping opens **`EditCardAfSheet`**
  (not `EditSubscriptionSheet`); all edits flow through
  `PATCH /cards/{id}`.
- A chat exchange "add my Amex Platinum, AF renews March 15"
  produces a parse card with the date pre-filled and the auto-log
  hint visible.
