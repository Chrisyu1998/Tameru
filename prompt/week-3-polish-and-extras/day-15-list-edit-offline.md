# Day 15 — Transaction list, edit sheet, and offline confirm queue

## Goal

The two transaction-history surfaces and the offline resilience layer for chat confirms:

- **Category Transaction List** (UX frame 11a) — reached by tapping "see all <category>" in the Breakdown Expanded view (UX frame 11). Filter chips, search, infinite scroll. The only place users browse arbitrary history.
- **Edit Transaction Sheet** (UX frame 11b) — opens from tapping any transaction row (in the list surface or in a chat-rendered candidate list). Edit fields, save, delete.
- **Offline queue for chat confirms** — when the user taps "looks right" on a parse card while offline, the confirm payload queues in IndexedDB and replays on reconnect. **Scope: confirms only, not composition.** Composing a new transaction or card requires connectivity because the parse step runs server-side in the Claude agent loop; the queue catches the narrow window between parse-card-render (online) and confirm-tap (offline). Fully-offline composition is not supported in v1 — see DESIGN.md §10.1.

There is **no `+`-button entry form** in v1 (CLAUDE.md invariant 8). Chat is the only user-initiated create path. This day ships the *retrieval, edit, and offline-confirm-sync* surfaces that complement it.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow + transaction list UX), §10.1 (offline scope — note the chat-unified clarification: confirms queue, composition requires online).
- `UX_PROMPT.md` frames 11, 11a, 11b, 15, 32.
- `CLAUDE.md` invariant 8.
- Day 10's `## Status (as built — 2026-05-14)` section in `prompt/week-2-chat-mvp-and-deploy/day-10-chat-ui-charts.md` — records that `frontend/src/components/EditTransactionSheet.tsx` and `frontend/src/components/chat/CandidateCards.tsx` already shipped, with chat-tap → edit-sheet → PATCH already wired. Day 15 extends those components, doesn't create them.
- Day 14's `POST /cards/confirm` 409 contract (`app/routes/cards.py:75-101`) — the drain loop has to treat `409 active_card_exists` as a successful dequeue, since the natural-key partial unique index (`cards_active_identity_uniq` from migration `20260516140000`) makes a "duplicate" card commit a no-op at the DB layer.

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

**Extend the existing `frontend/src/components/EditTransactionSheet.tsx`** (shipped in Day 10 — see Read first). The sheet, its 5-field layout (merchant · amount · date · card · category), the pencil glyphs, the Save-disabled-until-dirty affordance, the chat-tap entry point, and the `PATCH /transactions/:id` wiring are already in place. Day 15's work on this component is additive:

- Wire the **list-surface entry point**: tap a row in `CategoryTransactions.tsx` → open the existing sheet with that transaction's id (a second entry point alongside the existing chat-candidate-tap path).
- Add **swipe-delete confirm parity**: the same confirm dialog the sheet's Delete button fires must also fire from the list-row swipe-left path. Reuse one dialog component, not two.
- Confirm the category pill's enum source is `app/prompts/categories.py::ALLOWED_CATEGORIES` (not Lovable's `frontend/src/lib/categories.ts` divergent list — see Day 10 "Known bugs / open threads"). If those two lists are not unified by the time Day 15 lands, fix the divergence here.

Do not create a new sheet. Do not duplicate the PATCH wire.

### Frontend — chat candidate-list integration

`frontend/src/components/chat/CandidateCards.tsx` already shipped in Day 10 — render path, ledger injection, and tap → `EditTransactionSheet` are wired. Day 15's responsibility on this component is **verification only**:

- Add `tests/frontend/CandidateCards.test.tsx` asserting that a `get_transactions` multi-row tool result renders tappable rows and that the tap opens the sheet against the real row id. (Day 10 status section explicitly flagged this test as unwritten.)
- Regression-check that the tap still routes correctly after Day 15's list-surface entry point lands — the sheet now has two entry points, both must open against the right id.

Do not create `ChatTransactionCandidateCards.tsx`. The shipped name is `CandidateCards.tsx`.

### Frontend — offline confirm queue

`frontend/src/lib/offline_queue.ts`:

- **Storage shape.** IndexedDB-backed queue (`idb` library). Store: `pending_confirms`. Each entry: `{id: uuid, owner_user_id: uuid, kind: "transaction"|"card", payload: ProposalPayload, queued_at: ISO8601}`. The `kind` union deliberately omits `"subscription"` today — Day 19 extends both the queue schema and the drain with that branch when `POST /subscriptions/confirm` lands. Shipping the subscription branch before its confirm endpoint exists means the drain would POST to a 404.
- **Idempotency.** For transactions, `payload.client_request_id` is the server-side idempotency key (Day 5 / §8.2) and must be preserved unchanged across queue → replay — don't regenerate it on drain. For cards, there is no `client_request_id`, but the partial unique index `cards_active_identity_uniq` on `(user_id, issuer, last_four) WHERE active=true` (migration `20260516140000`) makes a replay deterministic at the DB level — the route returns `409 active_card_exists` and the drain treats that as a successful dequeue (see below).
- **Trigger.** `window.addEventListener("online", drainQueue)` in the React shell. Main-thread drain, not a service worker. Rationale: the SW can't read the in-memory Supabase session token, iOS Safari Background Sync is unreliable enough we can't depend on it (DESIGN.md §10.2), and Tameru's single-active-device model (invariant 5) means the foreground tab is the right scope. Also drain on app mount when `navigator.onLine === true` to catch the "closed the tab offline, reopened it later online" case.
- **Enqueue.** When a confirm request (chat parse card "looks right") fails with a network error, push an entry to the queue with `owner_user_id = session.user.id` and render the `pending sync` badge (UX frame 32).
- **Drain semantics — FIFO by `queued_at`:**
  - **Skip cross-user entries.** Only drain entries whose `owner_user_id === session.user.id`. Other users' entries persist until they sign back in. Do not drain while signed out. This is the single rule that prevents a sign-out → sign-in-as-different-user flow from POSTing user A's queued confirm under user B's session.
  - **POST each matching entry in FIFO order** to its confirm endpoint (`POST /transactions/confirm` or `POST /cards/confirm`).
  - **2xx** → delete the entry. For transactions, the response carries `{transaction, insight}` — render the `EntryInsightBubble` in chat as normal (Day 13 fires the insight on first-successful commit; a race-y duplicate POST matched by `client_request_id` returns `insight: null` and is silently dequeued).
  - **409 `code: active_card_exists`** (cards only) → treat as successful dequeue. The user's card is already in the wallet from a prior drain attempt whose response was lost. Do not surface as an error.
  - **5xx / network error** → leave the entry in place; retry on the next `online` event or next app mount.
  - **422 / other 4xx** → pop the entry from the queue and re-render its proposal as a parse card in the chat thread with a quiet error line ("this couldn't sync — fix or discard"). Do not silently drop. The user is now online and can edit and re-tap "looks right" (which will succeed directly), or discard the parse card.
- **UI.** Persistent banner "X pending sync" — UX frame 32's "· 1 pending sync" micro-label generalizes; show the count. Banner clears when the queue is empty for the current user.
- **No JWT in IndexedDB.** The queue stores proposals + `owner_user_id` (Supabase user UUID, not a credential). Auth at drain time comes from the in-flight Supabase session.

### Tests

- `tests/frontend/CategoryTransactions.test.tsx` — filter chips update the list; search is debounced and fires `GET /transactions` with the right params; infinite scroll pulls the next page.
- `tests/frontend/EditTransactionSheet.test.tsx` (extend the existing test scaffold from Day 10 if present, otherwise create) — list-surface tap opens the sheet against the right id; swipe-delete and sheet-delete share one confirm dialog.
- `tests/frontend/CandidateCards.test.tsx` — multi-row `get_transactions` tool result renders tappable rows; tap opens the sheet against the real row id. Forwarded gap from Day 10.
- `tests/frontend/offline_queue.test.ts`:
  - Queue a transaction confirm offline → simulate `online` → POST fires once → queue empties.
  - Queue a card confirm → drain receives `409 active_card_exists` → entry is dequeued (treated as success, no error surfaced).
  - Queue → drain returns 500 → entry stays in queue and is retried on the next `online` event.
  - Queue → drain returns 422 → entry is removed from queue AND re-rendered as a parse card with an error affordance.
  - Two entries queued in order A then B → drain POSTs A before B (FIFO assertion).
  - User A queues an entry → A signs out → B signs in on the same device → drain does NOT POST A's entry → A signs back in → drain POSTs it.
  - Queue persists across page reload (IndexedDB property, not React state) — write, reload, assert the entry is still there before the drain runs.

## Don't

- Don't build a `+` button on the Home screen, and don't add a standalone `AddTransaction.tsx` page. The create path lives in chat (Day 10).
- Don't send the JWT into IndexedDB. The queue stores proposals, not auth — the in-flight Supabase session provides auth at replay time.
- Don't add receipt photo input today — Phase 2.
- Don't call `POST /transactions/confirm` without a prior proposal shape — every entry in the offline queue must already be a confirmed payload the user saw and approved in the chat UI.
- Don't regenerate `client_request_id` during queue drain. The id identifies the user's commit intent; regenerating it would defeat Day 5's idempotency and produce duplicate rows on network retries.
- Don't drain the queue while signed out, or while the signed-in user's `user.id` does not match a queue entry's `owner_user_id`. Cross-user drain is a data-integrity bug.
- Don't drain from a service worker. Window-scope only. The SW can't read the Supabase session token, and iOS Safari Background Sync isn't reliable enough to depend on (DESIGN.md §10.2).
- Don't include `kind: "subscription"` in this day's queue schema or drain. Day 19 extends both when `POST /subscriptions/confirm` exists. Shipping it early means the drain POSTs to a 404.
- Don't silently drop 422-failed entries. Re-surface them as parse cards with an error affordance so the user can fix or discard.
- Don't build an inline chat delete/update confirm card today. The chat-driven delete/update path in v1 is: agent `get_transactions(...)` → candidate cards → tap → edit sheet (this day) → Save/Delete. The inline confirm card for the exact-1 case is a post-launch enhancement (§6.2) and requires new agent tools (`propose_delete_transaction`, `propose_update_transaction`) not yet defined. The agent has no direct-mutate tools in v1 (see Day 9 and CLAUDE.md invariant 8).

## Done when

- From Breakdown Expanded, tapping "see all dining" opens the Category Transaction List filtered to Dining in the current month.
- Changing the month chip re-filters without a full-page reload.
- Typing in the search bar filters results with a 250ms debounce.
- Tapping a row opens the Edit Sheet. Changing a field enables Save. Saving fires `PATCH /transactions/:id` and closes the sheet.
- Swiping left on a row reveals the delete panel; confirming removes the row.
- Render 3 parse cards while online → switch to airplane mode → tap "looks right" on all 3 → re-enable network → all 3 sync in tap order, pending-sync banner counts 3 → 2 → 1 → 0 and clears.
- Render 1 parse card while online → switch to airplane mode → tap "looks right" → sign out → sign in as a different test user → confirm the entry does NOT drain → sign back in as the original user → entry drains.
- Render 1 parse card while online → switch to airplane mode → tap "looks right" → close the tab → reopen the tab while online → entry drains on app mount.
- Simulate a queued entry that fails with 422 on drain (server-side validation error). The entry is removed from the queue and re-renders as a parse card in chat with a "this couldn't sync" affordance; the user can edit and re-tap or discard.
- A queued card confirm that drains into `409 active_card_exists` is dequeued silently — the user does not see an error and the card already in their wallet stays as-is.
