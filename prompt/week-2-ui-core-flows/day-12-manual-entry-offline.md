# Day 12 — Transaction list, edit sheet, and offline confirm queue

## Goal

The two transaction-history surfaces and the offline resilience layer for chat confirms:

- **Category Transaction List** (UX frame 11a) — reached by tapping "see all <category>" in the Breakdown Expanded view (UX frame 11). Filter chips, search, infinite scroll. The only place users browse arbitrary history.
- **Edit Transaction Sheet** (UX frame 11b) — opens from tapping any transaction row (in the list surface or in a chat-rendered candidate list). Edit fields, save, delete.
- **Offline queue for chat confirms** — when the user taps "looks right" on a parse card while offline, the confirm payload queues in IndexedDB and replays on reconnect.

There is **no `+`-button entry form** in v1 (CLAUDE.md invariant 8). Chat is the only user-initiated create path. This day ships the *retrieval, edit, and offline-confirm-sync* surfaces that complement it.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow + transaction list UX), §10.1 (offline requirements).
- `UX_PROMPT.md` frames 11, 11a, 11b, 15, 32.
- `CLAUDE.md` invariant 8.

## Deliverables

### Frontend — Category Transaction List (UX frame 11a)

`frontend/src/pages/CategoryTransactions.tsx`:

- Route: `/breakdown/:category`. Reached from Breakdown Expanded (frame 11) "see all <category>" link.
- Top bar: back chevron + category name (Fraunces lowercase, e.g. "dining").
- Filter chips row: month selector pill (current month default; options: current month, previous month, last 90 days, all time) + card filter pill (default "all cards").
- Search bar below chips (sunken, pill-shape, magnifying glass, placeholder "search merchant…"). Fires `GET /transactions?category=<cat>&merchant_contains=<q>&...` debounced 250ms.
- Scrollable list: each row shows date-day tertiary ("Apr 18"), merchant primary, amount right-aligned (tabular-nums), card last-4 in tertiary micro. Hairline dividers.
- Infinite scroll: fetch 50 at a time via `offset`/`has_more` from `GET /transactions` (Day 5).
- Tap row → open edit sheet (below).
- Swipe-left on a row → inline terracotta delete panel → tap to confirm → `DELETE /transactions/:id`.
- Bottom nav.

### Frontend — Edit Transaction Sheet (UX frame 11b)

`frontend/src/components/EditTransactionSheet.tsx`:

- Bottom sheet over a scrim. Drag handle + close-X.
- Five editable field rows, same order as the chat parse card: merchant (text) · amount (numeric keyboard) · date (calendar picker) · card (pill opening a card dropdown of the user's `GET /cards`) · category (pill opening the Day 4 closed enum dropdown).
- Each row has a pencil glyph; tapping anywhere on the row makes the field editable.
- Bottom action row: **Save** (accent, disabled until any field differs from the original) · **Cancel** (secondary) · **Delete** (terracotta text link, far right).
- Save → `PATCH /transactions/:id` with the changed fields. Delete → same confirm flow as swipe-delete.
- Used by both the list surface and by chat-rendered candidate cards (see below).

### Frontend — chat candidate-list integration

`frontend/src/components/ChatTransactionCandidateCards.tsx`:

- Rendered inside the chat thread when a `get_transactions` tool result returns multiple rows and the agent's surrounding prose suggests disambiguation ("I see three coffees around that time, which one?"). Day 18 owns the chat thread plumbing; this day builds the candidate-card component and its tap-to-edit wiring.
- Each candidate card renders the same 5 fields as the list row (more compact) with a "tap to edit" affordance that opens the `EditTransactionSheet` with that transaction's id.

### Frontend — offline confirm queue

`frontend/src/lib/offline_queue.ts`:

- IndexedDB-backed queue (`idb` library). Store: `pending_confirms`. Each entry: `{id: uuid, kind: "transaction"|"card"|"subscription", payload: ProposalPayload, queued_at}`.
- On a confirm request (chat parse card "looks right") failing with a network error, push to queue and render the `pending sync` badge (UX frame 32).
- Service worker `online` event handler: drains the queue, POSTs each to the matching confirm endpoint (`POST /transactions/confirm`, `POST /cards/confirm`, `POST /subscriptions/confirm`), removes on success.
- On replay success for a transaction confirm, the server's entry-moment insight (Day 13) comes back with the response and is rendered into the chat retroactively — or silently dropped if the user has long since moved past the conversation. Acceptable either way; don't replay stale toasts.
- UI: persistent banner "X pending sync" (UX frame 32's "· 1 pending sync" micro-label on the Home dashboard is the reference copy).

### Tests

- `tests/frontend/CategoryTransactions.test.tsx` — filter chips update the list; search is debounced and fires `GET /transactions` with the right params; infinite scroll pulls the next page.
- `tests/frontend/EditTransactionSheet.test.tsx` — save button is disabled until a field changes; save fires `PATCH /transactions/:id` with only the changed fields; delete opens the confirm dialog.
- `tests/frontend/offline_queue.test.ts` — queue a confirm while offline, simulate `online` event, assert the POST fires once and the queue empties. Replay after a 500 does NOT dequeue.

## Don't

- Don't build a `+` button on the Home screen, and don't add a standalone `AddTransaction.tsx` page. The create path lives in chat (Day 18).
- Don't send the JWT into IndexedDB. The queue stores proposals, not auth — the in-flight Supabase session provides auth at replay time.
- Don't add receipt photo input today — Phase 2.
- Don't call `POST /transactions/confirm` without a prior proposal shape — every entry in the offline queue must already be a confirmed payload the user saw and approved in the chat UI.
- Don't support chat-driven edits or deletes in this UI — edits open the sheet on tap; deletes use swipe or the sheet's delete button. The chat agent has no `edit_transaction` or `delete_transaction` tool (see Day 16 "Don't").

## Done when

- From Breakdown Expanded, tapping "see all dining" opens the Category Transaction List filtered to Dining in the current month.
- Changing the month chip re-filters without a full-page reload.
- Typing in the search bar filters results with a 250ms debounce.
- Tapping a row opens the Edit Sheet. Changing a field enables Save. Saving fires `PATCH /transactions/:id` and closes the sheet.
- Swiping left on a row reveals the delete panel; confirming removes the row.
- Airplane mode → chat-confirm 3 transactions → re-enable network → all 3 sync, pending-sync banner clears.
