# Day 10 — Chat UI (full): thread, write flows, candidate cards, charts

## Goal

The full chat experience. Chat is the only user-initiated write surface in v1 (CLAUDE.md invariant 8), so this day covers more than Q&A — it renders the **parse card** (UX frame 15) for `propose_*` tool results, the **candidate list** when `get_transactions` returns multiple rows, and the **entry-moment insight bubble** after a confirm. Plus the original read-path features: tool_use indicator, inline Recharts for generative charts. **Wire mode today is non-streaming** (one request → one full reply) against Day 8's `/chat/turn`; Day 12 upgrades the wire to SSE and swaps in token-by-token rendering without touching the ParseCard/CandidateList/Insight/Chart surfaces shipped here.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow), §7.1–7.2 (agent loop + tools + propose-then-confirm), §7.8 (generative charts).
- `UX_PROMPT.md` frames 12–16.
- `CLAUDE.md` invariants 2, 8.

## Deliverables

### Chat thread and input

`frontend/src/pages/Chat.tsx`:

- Conversation thread (oldest top, newest bottom). Auto-scroll on new content.
- Input bar at bottom (multiline textarea; submit on Cmd/Ctrl+Enter; tap-to-send button). Mic button integration belongs to Day 18; this day wires a placeholder slot.
- **Non-streaming wire for now.** POST to Day 8's `/chat/turn` and render the full assistant reply when it arrives. The response shape is `{conversation_id, assistant_text, tool_calls: [{name, input, result}]}` — `assistant_text` is the prose bubble, and `tool_calls` is the array Day 10 iterates to render ParseCard (for `propose_*` results) or CandidateList (for `get_transactions` with multiple rows). No progressive token rendering today. Day 12 converts `/chat/turn` to SSE and swaps this in for a token-by-token render; the rest of Day 10 (ParseCard, CandidateList, EntryInsightBubble, conversation switching) stays intact across that upgrade — Day 12's `done` event carries the same `tool_calls` payload.
- Tool-use indicator: while the turn is in flight, show a small quiet pill in the bubble (e.g. "thinking…"). Day 12 refines this to show per-tool pills ("looking up dining transactions…") once tool-use events stream individually.
- Conversation list: left drawer (mobile: top dropdown) showing prior conversations by title. New-conversation button.

`frontend/src/components/ChatThread.tsx`: reusable, also used by the guided tour (Day 21).

### Inline cards: write flows (parse card, candidate list)

This is the core addition that makes chat a complete write surface.

`frontend/src/components/ParseCard.tsx`:

- Rendered when a `tool_result` for `propose_transaction`, `propose_card`, or `propose_subscription` arrives. Distinguishes the three by `kind` on the payload.
- For `transaction` kind (UX frame 15): five rows (merchant / amount / date / card / category) each with a pencil glyph, editable inline. Action row: "let me fix it" secondary + "looks right" primary.
- For `card` kind: network · last-4 · program · multipliers (as multiplier chips) · annual fee · source URLs as citation links. Same "looks right" / "let me fix it" buttons.
- For `subscription` kind: name · amount · frequency · next billing · card · category. Same button pair.
- "Looks right" → calls the matching `POST /<resource>/confirm` endpoint (Day 5 / Day 14 / Day 19) with the proposal. **Transaction proposals only**: the `client_request_id` the `propose_transaction` tool generated (Day 9) round-trips unchanged on the confirm body, even if the user edited other fields via "let me fix it" — the id identifies the user's intent, not the payload, and powers server-side offline-replay idempotency (Day 5 / §8.2). Card and subscription proposals have no `client_request_id` by design (see Day 9 rationale). On success, the returned entity (and, for transactions, the entry-moment insight) is rendered into the chat.
- "Let me fix it" → inline edit of the card fields; "looks right" re-enables after edits. Editing a transaction parse card does not regenerate the `client_request_id`; a replay of a confirm that previously succeeded returns the original row (idempotency handled server-side by Day 5).
- **Nothing is written to the database from this component except via the confirm endpoints.** A parse card in the chat is a preview, never a row.
- **Stub note:** in the reordered plan, only `POST /transactions/confirm` (Day 5) exists by this day. Ship ParseCard rendering the `transaction` kind end-to-end, and leave `card` and `subscription` branches as a one-line placeholder (`"This ParseCard kind ships on Day 14 / Day 19."`). The branch-by-`kind` switch exists today so the additions are additive — Day 14 replaces the card placeholder with real rendering + a `POST /cards/confirm` call, Day 19 does the same for subscriptions. Do not wire up `POST /cards/confirm` or `POST /subscriptions/confirm` here; those endpoints don't exist yet.

`frontend/src/components/CandidateList.tsx`:

- Rendered when a `tool_result` for `get_transactions` returns multiple rows and Claude's surrounding prose suggests disambiguation.
- Each row: date · merchant · amount · card last-4 — compact, tappable. Tapping a row opens `EditTransactionSheet` (Day 15) for that transaction.
- The component is dumb: it renders whatever the tool result contained. Claude's prose ("I see three coffees around that time, tap one to edit") supplies the framing.
- **Stub note:** `EditTransactionSheet` (Day 15) lands after this day in the reordered plan. Ship `CandidateList` today rendering tappable rows, but make the tap a no-op (or route to a "coming soon" placeholder) until Day 15. For the first dogfood build, Claude's prose can ask a clarifying question instead of relying on tap-to-edit. Day 15 wires the tap to open the edit sheet in a one-line change.

### Entry-moment insight bubble

`frontend/src/components/EntryInsightBubble.tsx`:

- Rendered after a transaction confirm response (`POST /transactions/confirm` — Day 5) that returns a non-null `insight` field.
- One sentence, rendered as a quiet AI bubble below the confirmed transaction. Auto-fade-in on arrival. No buttons, no action.
- Does **not** render when `insight` is null (Day 13 returns null when nothing meaningful applies).
- Replaces the older "EntryInsightToast" plan from Day 13 — in the chat-unified UX, the insight lives inline in the chat, not as a dashboard toast.

### Generative charts (read-path feature)

`frontend/src/components/Chart.tsx`:

- Renders a Recharts component from a `ChartSpec` JSON: `{type: line|bar|stacked_bar|donut, x, series: [{name, data}], y_label, title}`.
- SVG only. Responsive to 375px viewport.

Backend: add a typed `render_chart(spec)` tool the agent can call (preferred over parsing `<chart>` blocks out of prose). The tool echoes the spec as a `tool_result`; the frontend extracts and renders via `Chart.tsx`.

### Daily cap UI

When a turn response (or in Day 12+, an SSE `error` event) surfaces `code: "DAILY_CAP_EXCEEDED"` (Day 9, DESIGN.md §11.2), render the inline frame-16 treatment: amber-tinted card replacing the input row with "you've used your daily ai quota" title and "resets at midnight utc" subtitle. No retry button. Other error codes get a generic "something went wrong — retry" handling; Day 12's SSE upgrade replaces this with the reconnect-button flow.

### Tests

- `tests/frontend/ParseCard.test.tsx` — rendering for each kind (transaction / card / subscription); "looks right" fires the correct confirm endpoint; "let me fix it" edit path preserves unedited fields; confirm success renders the insight bubble.
- `tests/frontend/CandidateList.test.tsx` — multiple-row tool_result renders tappable rows; tap opens edit sheet with the right id.
- `tests/frontend/Chart.test.tsx` — chart specs for line / bar / stacked_bar / donut render without errors at 375px.
- `tests/test_chart_spec.py` (backend) — "Chart my dining by week in March" produces a correct ChartSpec via `render_chart`.

## Don't

- Don't let `ParseCard` write to the database directly. The component posts to `POST /<resource>/confirm`; that endpoint is the only point of commit.
- Don't use Canvas-based chart libraries. SVG only (Recharts).
- Don't pre-compute charts on the backend. The agent decides; the frontend renders.
- Don't add chart export (PNG download). Out of scope for v1.
- Don't render the entry-moment insight as a blocking modal or a toast — it is a quiet chat bubble that a user can scroll past.
- Don't render a parse card with no "let me fix it" button even if Claude's confidence is high. The confirm UI is the point of commit regardless.

## Done when

- "spent $47 at Trader Joe's on my Amex Gold" → parse card (frame 15) with five editable rows → "looks right" → row committed → insight bubble rendered below.
- "change that $10 coffee from last week" (with seeded ambiguity) → Claude's prose plus a candidate list → tapping a candidate opens the edit sheet (Day 15).
- "add a card called Chase Sapphire Preferred" → parse card in `card` kind → "looks right" → card created.
- "chart my grocery spending by week in March" → inline line chart via `render_chart`.
- Switching conversations preserves their state.
- Hitting the daily cap during a turn renders the frame-16 treatment and freezes further sends until reset.

## Status (as built — 2026-05-14)

Day 10 landed alongside an unrelated frontend overhaul: the Lovable-mock import (steps 1–4 in `frontend-import-from-lovable`) replaced the original scaffold. As a result, the file paths and component boundaries differ from the spec above. The behavior is largely the same, but a future reader of the spec needs to look at the actually-shipped tree, not the planned one.

### Files that shipped (not what the spec named)

- Chat thread + input + busy indicator: [frontend/src/pages/chat.tsx](frontend/src/pages/chat.tsx) (Lovable scaffold, not the spec's `pages/Chat.tsx`).
- Shared chat session store: [frontend/src/lib/chatStore.ts](frontend/src/lib/chatStore.ts) — singleton + listener Set, hydrates the mobile route and the desktop `ChatDrawer` from one source of truth. Holds `messages`, `conversationId`, `busy`, and `drawerOpen / drawerExpanded`.
- Typed `/chat/turn` wrapper + error normalization: [frontend/src/lib/chatApi.ts](frontend/src/lib/chatApi.ts).
- Typed `/transactions` wrappers + camelCase↔snake_case + Decimal↔cents translation: [frontend/src/lib/transactionsApi.ts](frontend/src/lib/transactionsApi.ts).
- Ledger store, now backed by `GET /transactions` with auth-driven refresh, optimistic PATCH/DELETE, optimistic insert on confirm: [frontend/src/lib/ledger.ts](frontend/src/lib/ledger.ts).
- Parse card: [frontend/src/components/chat/ParseCard.tsx](frontend/src/components/chat/ParseCard.tsx) (Lovable; not `components/ParseCard.tsx`). Today renders only the `transaction` kind — card/subscription kinds aren't a Day 10 deliverable in this build (see deferred list below).
- Candidate list rendering: [frontend/src/components/chat/CandidateCards.tsx](frontend/src/components/chat/CandidateCards.tsx) (Lovable; not `CandidateList.tsx`).
- Edit sheet (used by candidate-tap, by chat "let me fix it", and by the dashboard/breakdown rows): [frontend/src/components/EditTransactionSheet.tsx](frontend/src/components/EditTransactionSheet.tsx). Accepts optional `onSave`/`onRequestDelete` overrides — the dashboard path PATCHes the row; the chat-draft path mutates the in-flight parse card via `chatStore.updateDraft()`.

### Deliverables shipped against spec

- **Chat thread**: built. POSTs to `/chat/turn` non-streaming; renders `assistant_text` + walks `tool_calls`. Auto-scrolls on new content.
- **Input bar**: built. Multiline textarea, Enter/Cmd-Enter submit, disabled while a turn is in flight.
- **Tool-use indicator**: built but **diverges from spec**. The "thinking…" pill renders above the input row, not as a small pill in the bubble. Works for the current non-streaming wire; revisit when Day 12 lands per-tool SSE events.
- **`POST /chat/turn` integration**: built. Persists server-minted `conversation_id` in `chatStore` and replays it on every subsequent turn so the agent loop sees the last 5 turns of history.
- **`ParseCard` for transactions (UX frame 15)**: built. Five editable rows, "looks right" + "let me fix it", no client-side writes. "Looks right" → `POST /transactions/confirm` with the `client_request_id` minted by `propose_transaction`, round-tripping unchanged across edits (idempotency invariant). On success, the returned row is optimistically inserted into the ledger.
- **"Let me fix it"**: built. Opens `EditTransactionSheet` on a synthetic temp `Transaction`; saves route to `chatStore.updateDraft()` (not the ledger), so the draft mutates in place and the eventual confirm carries the user's edited values. Delete on a draft calls `chatStore.discardDraft()` — no spurious DELETE request.
- **`CandidateList` rendering for `get_transactions`**: built. When the tool result returns ≥1 rows, the rows are injected into the ledger (so the candidate-card lookup resolves) and rendered as tappable rows. Tap opens `EditTransactionSheet` against the real row → `PATCH /transactions/{id}`. Intent defaults to `"edit"`; delete-intent inference is deferred (see day-10b).
- **Daily-cap UI / error handling**: partial. 429 `UCAP_EXCEEDED`, 503 `PROVIDER_RATE_LIMITED`, and 500 `LOOP_LIMIT` each surface as a specific inline text bubble in chat. The full frame-16 amber inline-replacement treatment is **not** built — the existing `DailyCapCard` component still renders only when the dev `sessionStorage` flag is set; it isn't wired to the real UCAP_EXCEEDED path yet.
- **Backend `/chat/turn`**: was already in place per Day 8 + 9a/b/c. No changes today.

### Deliverables explicitly NOT shipped (and where they go)

- **`ParseCard` for `card` and `subscription` kinds** — spec already deferred these to Day 14 / Day 19 (no `/cards/confirm` or `/subscriptions/confirm` endpoints exist yet). Today's `ParseCard` only renders the `transaction` kind; the `kind` switch is implicit (we only build parse messages when the agent emits `propose_transaction`). Day 14 / 19 wire the other branches.
- **`EntryInsightBubble`** — `POST /transactions/confirm` already returns `insight: string | null` per the Day 5 contract, but it's currently always null (Day 13 wires `entry_moment_insight()`). The bubble component doesn't exist yet; when Day 13 lands, plumb the field through `chatStore.commitDraft` and render below the confirmed parse card.
- **Generative charts** (`Chart.tsx`, `render_chart` agent tool) — not built. The `assistant_text` falls through to a plain text bubble for chart-shaped questions. Recharts is **not** in the package.json yet; add when Day 10's chart deliverable lands (see day-10b).
- **Conversation list / switching** — `chatStore.conversationId` is per-session; reloading the page loses both the conversation pointer and the visible messages. `chat_messages` rows exist server-side; we just don't fetch them. No `GET /chat/messages` endpoint exists either — needs a small backend addition.
- **Tests** — `ParseCard.test.tsx`, `CandidateList.test.tsx`, `Chart.test.tsx`, `test_chart_spec.py` are not written. Typecheck + production build are clean; behavior is verified manually.

### Bonus work outside the Day 10 spec

These shipped alongside Day 10 because the Lovable-mock import made them load-bearing for chat-confirm to work at all:

- **Real-data ledger**: `useLedger()` now sources from `GET /transactions`. The store subscribes to JWT changes and refetches; sign-out clears the cache. The hook signature is preserved so all dozen Lovable consumers (home, breakdown, cards, sidebar, chat candidate lookup) keep working unchanged.
- **`card_id` UUID sanitization at the wire boundary**: Lovable's `FIXTURE_CARDS` use slug ids like `card-amex`. There's no `/cards` endpoint yet, so any non-UUID `cardId` is downgraded to `null` on the way out of `confirmTransaction` / `patchTransaction`. See `sanitizeCardId()` in `transactionsApi.ts`.
- **`claim_device` wired into `refreshHomeCurrency`**: when a returning user signs in, the new `initAuth → refreshHomeCurrency` path calls `POST /auth/claim_device` with the browser's `deviceId` and clears the local `displaced` flag on success. Without this a fresh-localStorage browser deadlocks against a server-side `active_device_id` from a prior session. Original Splash had this; reintroduced after the Lovable replace dropped it.

### Known bugs / open threads (forwarded to day-10b)

- Frontend `CATEGORIES` (`frontend/src/lib/categories.ts`) ≠ backend `ALLOWED_CATEGORIES` (`app/prompts/categories.py`). Only `Groceries / Dining / Travel` overlap. Agent proposals in `Coffee Shops / Gas / Transit / Streaming / ...` get silently coerced by Lovable's `<select>` to whatever's at the top of its own list, and any user pick from Lovable-only entries (`Transportation`, `Entertainment`, etc.) 422s on confirm. **Resolution: unify the frontend list with the backend.**
- `POST /transactions/confirm` was observed returning 422 in manual testing on 2026-05-14. Response body never captured, so root cause is unconfirmed — likely the category-enum issue above, but possibly something else. Re-test after the category unification.
- Chat history doesn't survive a page refresh (no `GET /chat/messages`).
