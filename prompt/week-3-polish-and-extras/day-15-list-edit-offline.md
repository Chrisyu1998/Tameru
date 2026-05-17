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
- The proposal-annotation pipeline in `app/routes/chat.py` — `_persist_turn` (~lines 317-391) embeds the agent's *original* `tameru_proposal` blocks on the assistant `chat_messages` row at persist time, and `_annotate_committed_proposals` (~lines 534-652) stitches `committed_id` / `committed_state` onto them on rehydrate by looking up rows by `client_request_id` (transactions) or `name` (cards). Day 15 extends the second helper to also carry the committed row's *actual field values*, so a rehydrated card never displays the agent's original suggestion when the user edited the draft before tapping "looks right." Read `_wireMessageToLocal` and `_proposalToDraft` in `frontend/src/lib/chatStore.ts` for the consumer side — those build the rehydrated `ParseDraft` from the block's `result` today and need to prefer `committed_payload` after this change.

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
- **Idempotency.** For transactions, `payload.client_request_id` is the server-side idempotency key (Day 5 / §8.2) and must be preserved unchanged across queue → replay — don't regenerate it on drain. Cards also carry a `client_request_id` (migration `20260517120000_cards_client_request_id.sql`); same rule applies — preserve it across queue → replay so the route's same-crid short-circuit fires cleanly. The natural-key partial unique index `cards_active_identity_uniq` on `(user_id, issuer, last_four) WHERE status='active'` remains the structural dedup for "different proposals for the same physical card" — the route returns `409 active_card_exists` in that case and the drain treats it as a successful dequeue (see below).
- **Trigger.** `window.addEventListener("online", drainQueue)` in the React shell. Main-thread drain, not a service worker. Rationale: the SW can't read the in-memory Supabase session token, iOS Safari Background Sync is unreliable enough we can't depend on it (DESIGN.md §10.2), and Tameru's single-active-device model (invariant 5) means the foreground tab is the right scope. Also drain on app mount when `navigator.onLine === true` to catch the "closed the tab offline, reopened it later online" case.
- **Enqueue.** When a confirm request (chat parse card "looks right") fails with a network error, push an entry to the queue with `owner_user_id = session.user.id` and render the `pending sync` badge (UX frame 32).
- **Drain semantics — FIFO by `queued_at`:**
  - **Skip cross-user entries.** Only drain entries whose `owner_user_id === session.user.id`. Other users' entries persist until they sign back in. Do not drain while signed out. This is the single rule that prevents a sign-out → sign-in-as-different-user flow from POSTing user A's queued confirm under user B's session.
  - **POST each matching entry in FIFO order** to its confirm endpoint (`POST /transactions/confirm` or `POST /cards/confirm`).
  - **2xx** → delete the entry. For transactions, the response carries `{transaction, insight}` — render the `EntryInsightBubble` in chat as normal (Day 13 fires the insight on first-successful commit; a race-y duplicate POST matched by `client_request_id` returns `insight: null` and is silently dequeued). **Also locate the matching in-memory parse-card message and flip it to committed.** For transactions, match by `client_request_id` (the draft carries it after Day 14b rehydrate). For cards, match by the in-memory message id captured at enqueue time. Set `committedTxId` / `committedCardId` to the returned row's id, and overwrite the message's local draft fields (merchant, amount, date, card_id, category, notes for transactions; network, last_four, name, issuer, program, multipliers, annual_fee, alias for cards) from the *response payload* — not the queued body, the response, so any server-side normalization (e.g., merchant canonicalization) wins over the client's pre-send shape. Without this patch the user would see `not saved.` on the parse card sitting directly above a successful `EntryInsightBubble` until the page is reloaded, because nothing else in the session knows the queued tap finally landed.
  - **409 `code: active_card_exists`** (cards only) → treat as successful dequeue. The user's card is already in the wallet from a prior drain attempt whose response was lost. Do not surface as an error.
  - **5xx / network error** → leave the entry in place; retry on the next `online` event or next app mount.
  - **422 / other 4xx** → pop the entry from the queue and re-render its proposal as a parse card in the chat thread with a quiet error line ("this couldn't sync — fix or discard"). Do not silently drop. The user is now online and can edit and re-tap "looks right" (which will succeed directly), or discard the parse card.
- **UI.** Persistent banner "X pending sync" — UX frame 32's "· 1 pending sync" micro-label generalizes; show the count. Banner clears when the queue is empty for the current user.
- **No JWT in IndexedDB.** The queue stores proposals + `owner_user_id` (Supabase user UUID, not a credential). Auth at drain time comes from the in-flight Supabase session.

### Rehydrate truth — `committed_payload` annotation

The parse card the user edits is a frontend artifact; only the POST body sent to `/transactions/confirm` carries those edits. The persisted `tameru_proposal` block on `chat_messages` was written at proposal time and freezes the agent's *original* suggestion. Without an additional annotation, a rehydrated `logged.` parse card displays the original amount even though the ledger row holds the edit — and a "queued offline, app closed, reopened later" sequence renders `not saved.` over the original amount while the drain quietly commits the edit on mount. Fix both sides:

**Backend** (`app/routes/chat.py::_annotate_committed_proposals`):

- Widen the `transactions` select from `id, client_request_id, status, deleted_at` to also include `amount, merchant, date, category, card_id, notes`. Widen the `cards` select symmetrically: `id, name, status, deleted_at, network, last_four, issuer, program, multipliers, annual_fee, source_urls, alias`.
- When a `tameru_proposal` block matches a committed row, stitch a new `committed_payload` dict onto the block alongside `committed_id` and `committed_state`. Additive — `input` and `result` stay as the agent's original proposal (audit/replay trace property).
- Continue reading from the base `transactions` / `cards` tables (DESIGN.md §8.2 — already the case). A `deleted.` badge with stale display values is still wrong.

**Frontend** (`frontend/src/lib/chatStore.ts`):

- In `_proposalToDraft` and `_proposalToCardDraft`, when the synthetic block carries `committed_payload`, build the rehydrated `ParseDraft` / `CardParseDraft` from it instead of from `call.result`. When absent (uncommitted proposal that never confirmed, or any pre-Day-15 row), fall back to `call.result` as today — preserves the `not saved.` rendering for never-confirmed cards and keeps the migration backward-compatible.
- Confidence values stay at the cosmetic `0.95` ceiling — the pencil glyphs don't matter on a frozen card, but flipping them to a different number on rehydrate would imply we re-ran the model.

Together, the backend annotation is the load-bearing fix for the close-app-then-reopen replay case (drain runs on mount before the user sees the chat; hydrate-with-`committed_payload` ensures the rehydrated card renders the *committed* values). The drain handler's in-memory patch (above) covers the same-session reconnect case where hydrate hasn't re-run since the row was written. Both are needed.

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
  - **Edit-then-queue patches the in-memory card on drain.** Render a fresh parse card with proposal amount $40 → user edits to $42 → goes offline → taps "looks right" → reconnect → drain → assert the matching in-memory message has `committedTxId` set AND its local draft renders $42 (not $40). No page reload between edit and assertion.
- `tests/frontend/ParseCardRehydrate.test.tsx` (new) — `_wireMessageToLocal` on a `tameru_proposal` block whose `committed_payload.amount` differs from `result.amount` produces a `ParseDraft` carrying the `committed_payload` amount. When `committed_payload` is absent, falls back to `result` (backward-compat). Symmetric case for `propose_card`.
- `tests/routes/test_chat_messages.py` (extend existing — the `/chat/messages` rehydrate test surface) — `_annotate_committed_proposals` returns blocks with `committed_payload` whose fields match the actual `transactions` / `cards` row, not the proposal `input`. Specifically: write a transaction at amount $42 with a known `client_request_id`, then call the helper on a synthetic block whose `result.amount` is $40 — the returned block has `committed_payload.amount == 42`. Same shape for cards keyed on `name`.

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
- Don't trust `input` or `result` over `committed_payload` when rendering a rehydrated parse card. `input`/`result` is the agent's *suggestion*; `committed_payload` is the *truth* in the ledger. When the two disagree, the user edited before confirming — display the edit. Leave `input`/`result` on the wire as the audit/replay artifact.
- Don't try to close the edit-then-rehydrate gap purely in the drain handler. The in-memory patch only covers sessions that stayed open across reconnect; the close-tab-then-reopen case must come from the backend `committed_payload` annotation because hydrate runs before drain on mount. Both fixes ship in the same day; neither is optional.

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
- Render a parse card while online with proposal amount $40 → edit it to $42 → switch to airplane mode → tap "looks right" → re-enable network → the parse card flips to `logged.` displaying **$42 (the edit)**, not $40 (the original proposal), in the same session without a page reload. The ledger row is $42. Reload the page → the rehydrated parse card still renders `logged. $42`.
- Same flow but close the tab between "looks right" and reconnect: reopen the tab while online → mount-time drain commits the $42 row → reload once more so the chat rehydrates from the now-committed proposal → the rehydrated parse card renders `logged. $42`, **not** `logged. $40` and **not** `not saved.`. The number on the card matches the number in the ledger and in the dashboard.
