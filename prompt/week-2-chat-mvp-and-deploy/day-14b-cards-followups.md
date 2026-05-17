# Day 14b — Card follow-ups: breakdown filter semantics, collision deep-link, cleanups

## Goal

Day 14 shipped the backend (migration, integration, routes, `propose_card`)
and the on-ramp surfaces (onboarding AddCardStep, post-onboarding Cards
page wired to real `/cards`). Three deliverables were deferred for scoping
reasons; this prompt picks them up plus a couple of small cleanups.

The big one is **frontend filter semantics on the breakdown surfaces**
(DESIGN.md §8.1 Rules 1–3): the dashboard / category drill-down / chat-
rendered card breakdowns must keep showing soft-deleted cards in totals,
list them in the filter dropdown when they have transactions in scope,
and label them with a `closed {MMM YYYY}` suffix. None of that is wired
yet — backend support (`GET /cards?include_inactive=true`,
`deactivated_at` on the response) is in place from Day 14.

## Read first

- `DESIGN.md` §8.1 frontend filter rules (Rules 1–3) and "Soft-delete /
  re-add semantics."
- `DESIGN.md` §6.1 (web_search-backed lookup, fallback path).
- Day 14 prompt (`day-14-cards-perplexity.md`) — context, especially the
  frontend filter semantics subsection.
- Existing breakdown pages: `frontend/src/pages/breakdown.index.tsx`
  and `frontend/src/pages/breakdown.category.tsx`.
- Existing cards page: `frontend/src/pages/cards.tsx`.
- Existing chat-rendered card breakdowns: `frontend/src/components/chat/Chart.tsx`
  (the `donut` and `bar` card-keyed paths in particular).

## Deliverables

### 1. Wire breakdown surfaces to the active+inactive card list

The current code path: `useLedger().cards` returns active cards only.
That means transactions whose `card_id` points at a soft-deleted card
either render as "Other / Cash" (wrong — they had a real card) or get
hidden from filters entirely. Both break Rule 1 (totals must reconcile).

- Add a second selector `useLedger().allCards` (or extend the existing
  shape) backed by `apiListCards({ includeInactive: true })`. Fetch on
  JWT change alongside the existing `refreshCards()`. Don't replace the
  default `cards` view — the post-onboarding cards page (UX frame 18)
  still wants active-only.
- `pages/breakdown.index.tsx`: the per-card breakdown widget reads from
  the union of active + inactive cards (Rule 1). Sum-by-card aggregates
  must include rows whose `cardId` points at an inactive card.
- `pages/breakdown.category.tsx`: filter dropdown (if present) follows
  Rule 2 — show all active cards always, plus inactive cards whose
  `card.id` appears in `transactions.cardId` for the visible date
  range. Inactive cards with no transactions in scope are hidden.

### 2. Render the `closed {MMM YYYY}` suffix on inactive cards (Rule 3)

- Add a `closedAtLabel(card)` helper that derives the suffix from
  `card.deactivatedAt` (needs to be plumbed through
  `cardRowToFixture()` in `ledger.ts`; today's mapper drops it).
- Update tile / chip / list-row renderers that show inactive cards in
  breakdown views to render `{card.name} · {last4} · closed {MMM YYYY}`
  in a muted color (e.g. `text-ink-tertiary` plus reduced opacity).
- The cards page (UX frame 18) does NOT render inactive rows — that
  list is the user's *live* wallet. Filter semantics apply only on
  breakdown surfaces.

### 3. Deep-link the 409 collision to the cards page

Day 14's AddCardStep currently surfaces the 409 with a text banner
("you already have *X* ending NNNN — edit that one from the cards page").
There's no clickable target.

- Use `react-router-dom` `Link` to deep-link the banner to
  `/cards#{existing_card_id}` (or `?focus={existing_card_id}` —
  hash routing is simpler).
- `pages/cards.tsx` reads the focus param on mount and scrolls the
  matching tile into view + flashes a brief highlight ring.
- Same affordance on the chat parse card: when `propose_card` triggers
  a confirm that 409s (post-launch enhancement; chat won't 409 today
  because `propose_card` never commits — the commit fires from the
  parse-card confirm tap, which is a separate `POST /cards/confirm`
  that *can* 409). When that confirm 409s, the parse card renders the
  same inline banner with a deep-link.

### 4. Add a web_search org-enablement smoke check

DESIGN.md §16 flags the one-time Claude Console enablement as a known
open item. Today there's no automated check; if an operator forgets to
enable it, the first card lookup users try silently falls back to
manual entry.

- Add `scripts/smoke_web_search.py` that fires one `lookup_card("Chase
  Sapphire Reserve", <bootstrapped test user>)` and prints PASS/FAIL.
- README note: run after rotating `ANTHROPIC_API_KEY` or onboarding
  a new Anthropic org.
- Out of scope: a recurring monitor — manual is fine at v1.

### 5. Delete orphaned fixtures

The Day 14 rewrite of `AddCardStep` removed the only consumer of:
- `frontend/src/features/onboarding/cardFixtures.ts`
- `frontend/src/features/onboarding/CardReviewTile.tsx`

Both files are now self-referential only (the fixture imports its own
types, the tile imports from the fixture). Delete both. Verify with a
final `grep -r "CardReviewTile\|fetchCardPreview\|cardFixtures"` from
the repo root — should return zero hits after the delete.

### 6. Tighten the undo grace path

Today: `ledger.deleteCard(id)` optimistically removes the card from
local state + schedules `DELETE /cards/:id` after 5s. `ledger.insertCard`
cancels the timer if undo fires in time. If the timer fires before undo
(or the user undoes after the API delete completes), `insertCard`'s
local re-insert *looks* correct until the next page refresh, when the
soft-deleted row disappears.

Pick one of:
- **Accept the paper-cut.** Document the behavior in `ledger.ts` and
  move on. v1-acceptable.
- **Surface "deletion is permanent in 5s" in the undo toast.** Cheap UX
  fix; tells the user the window is real.
- **Pre-commit confirm.** Pop a confirm dialog before the optimistic
  delete; once confirmed, no undo. Inverts the undo pattern but matches
  what users typically expect for "real" deletes.

Default to option 1 unless the team has a stronger opinion. Don't add a
"revive soft-deleted card" code path — DESIGN.md §8.1 invariant.

### Tests

- `tests/test_breakdown_filter_semantics.py` (or extend existing tests):
  - Insert 2 active cards + 1 inactive card with transactions on each.
  - `GET /breakdown/categories` (or whichever endpoint feeds the per-
    card breakdown) sums across all 3 cards.
  - Filter dropdown returns active cards + the inactive card (because it
    has transactions in scope).
  - With the date range narrowed to exclude the inactive card's
    transactions, the inactive card drops out of the dropdown.
- Frontend snapshot/visual test for the `closed {MMM YYYY}` label.

## Don't

- Don't filter transactions by `cards.active` in any breakdown SQL —
  Rule 1 mandates totals reconcile across active + inactive.
- Don't render inactive cards on the live cards page (UX frame 18).
  That list is the user's wallet; soft-deleted rows belong only to the
  historical breakdown.
- Don't revive soft-deleted card rows on re-add. New row, fresh
  `card_id`. DESIGN.md §8.1 invariant.
- Don't add backend support for editing `network`, `last_four`, or
  `deactivated_at` via PATCH. Identity fields stay immutable; the
  delete-then-re-add path is the only way to "change" them.

## Done when

- Spending breakdown surfaces show inactive cards in totals (Rule 1).
- The filter dropdown adds inactive cards only when they have
  transactions in the current view (Rule 2).
- Inactive rows in the breakdown render as
  `{name} · {last4} · closed {MMM YYYY}` in a muted color (Rule 3).
- The cards page (`/cards`) still shows only active cards.
- The 409 collision banner in AddCardStep is a clickable deep-link to
  the existing card's tile on the cards page.
- `scripts/smoke_web_search.py` exists and exits 0 on a working setup.
- `cardFixtures.ts` and `CardReviewTile.tsx` are deleted; no consumers
  remain.
- A decision on the undo-grace UX is made and documented in
  `ledger.ts`.
