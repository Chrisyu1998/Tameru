# Day 10b — Chat UI completion: enum unification, daily-cap UI, conversation history, generative charts

## Goal

Close out the parts of Day 10 that didn't land in the initial cut, plus fix the live bugs surfaced during manual testing on 2026-05-14. The deliverables here are independent — pick them off in any order — but they collectively complete the Day 10 spec. After 10b lands, the chat surface is feature-complete for v1's read + transaction-write paths; only card/subscription parse cards (Day 14 / Day 19) and streaming (Day 12) remain as scheduled future work.

## Read first

- `day-10-chat-ui-charts.md` — the original spec, including the "Status (as built — 2026-05-14)" section at the bottom that enumerates what's already done.
- `DESIGN.md` §6.2, §7.1–7.2, §7.8 (generative charts), §8.11–8.12 (chat_messages / chat_turn_trace).
- `UX_PROMPT.md` frames 12–16.

## Deliverables

### 1. Unify the category enum (frontend ↔ backend)

**This is the blocking bug.** The frontend's `CATEGORIES` list in [frontend/src/lib/categories.ts](frontend/src/lib/categories.ts) was inherited from the Lovable mock and doesn't match the backend's `ALLOWED_CATEGORIES` in [app/prompts/categories.py](app/prompts/categories.py). Only `Groceries / Dining / Travel` overlap. The mismatch produces two failure modes:

- The agent legitimately proposes `Coffee Shops`, `Gas`, `Transit`, `Streaming`, etc. The Lovable `<select>` doesn't include those options, so on first edit the picker silently coerces the draft to its own first option (`Groceries`).
- If the user picks one of the Lovable-only entries (`Transportation`, `Entertainment`, `Shopping`, `Utilities`, `Health`, `Subscriptions`, `Other`), `POST /transactions/confirm` 422s with `category 'X' is not in the closed enum`.

**The backend is the source of truth** — DESIGN.md §6.2 entry-moment insight + card-reward multiplier groupings are built on the MCC-aligned taxonomy in `ALLOWED_CATEGORIES`. Frontend gets unified to match.

Concretely:

- Replace `CATEGORIES` in `frontend/src/lib/categories.ts` with the full backend list (read it from `app/prompts/categories.py` and mirror exactly — no extras, no renamings). Keep the `as const` + `Category` type derivation.
- Remap `CATEGORY_TINT` and `CATEGORY_SKETCH` — pick tints/sketches for the new categories the Lovable mock didn't have (`Coffee Shops`, `Gas`, `Transit`, `Streaming`, etc.). Reuse the existing palette tokens (`--moss`, `--over`, `--warn`, etc.); don't invent new colors.
- Search the codebase for hardcoded references to Lovable-only categories — `Transportation`, `Entertainment`, `Shopping`, `Utilities`, `Health`, `Subscriptions`, `Other`. Replace with the closest backend equivalent or drop the reference. Likely hit list: `frontend/src/lib/fixtures.ts` (`CATEGORY_BASELINES`), the home page's category tile logic, the breakdown pages.
- Remove the `as Category` cast in `chatStore._proposalToDraft` and `transactionsApi.fromWire` once the type covers every backend value; if a server row still returns something outside the union it's a real bug, not a cast to paper over.

**Verify**: after this lands, retry the original 422 — `coffee $5.5` → "looks right" — and watch `POST /transactions/confirm` return 200. If it still 422s, capture the response body (`detail` array) and we have a different root cause to chase. The 2026-05-14 422 was never confirmed to be the category enum; it's the leading hypothesis.

### 2. Wire `UCAP_EXCEEDED` to the frame-16 DailyCapCard

The `DailyCapCard` component already exists ([frontend/src/components/chat/DailyCapCard.tsx](frontend/src/components/chat/DailyCapCard.tsx)) and renders the spec's frame-16 amber-tinted treatment, but it's currently gated on a dev-only `sessionStorage` toggle (`isDailyCapEngaged()` in `lib/chat.ts`). The real `UCAP_EXCEEDED` 429 from `/chat/turn` falls through to a plain text bubble.

Replace the gating: hold a `capEngaged: boolean` field on the `chatStore` state. Set it to `true` when `postChatTurn` rejects with `{code: "UCAP_EXCEEDED"}`. Surface it via `useChatStore()` so [pages/chat.tsx](frontend/src/pages/chat.tsx) renders `<DailyCapCard />` in place of the `<InputRow />` when set (the existing render switch already has the right shape; just swap the predicate).

Reset `capEngaged` to `false` on `chatStore.newChat()` and on the next successful turn. Keep the dev `sessionStorage` toggle around — it's a useful dev affordance for testing the UI without burning real Anthropic tokens.

The spec also calls out "resets at midnight utc" subtitle. The 429 response carries the next reset time? If not, hardcode "resets at midnight utc" — that's literally what the backend's daily-bucket cap does.

### 3. Chat conversation history persistence

Right now reloading the chat page drops both `chatStore.messages` and `chatStore.conversationId`. The server has the history in `chat_messages` (DESIGN.md §8.11) but the frontend never reads it.

Two pieces:

- **Backend**: add `GET /chat/messages?conversation_id=<uuid>` to [app/routes/chat.py](app/routes/chat.py). Returns `{messages: [{role, content_blocks, created_at}], ...}` ordered chronologically. RLS scopes the read via the user's JWT. Cap at the most recent N rows (start with 50). The shape mirrors what `_persist_turn` writes — `content_blocks` is the assistant's final-iteration block list (a tool_use + tool_result trace is **not** surfaced here; that's `chat_turn_trace` and stays internal).
- **Frontend**: add a thin `getChatMessages(conversationId)` wrapper in `lib/chatApi.ts`. On chat-page mount, if `chatStore.conversationId` is set but `messages` is empty, fetch and hydrate. Persist `conversationId` in `localStorage` (key: `tameru-chat-conversation-id`) so a refresh can rehydrate the same conversation. Map server `content_blocks` to the local `ChatMessage` union — for v1, all blocks render as text bubbles; parse cards and candidate lists are not rehydrated because their interactive state (committed flag, expanded fields) is per-session.

Note: the goal isn't perfect rehydration. It's that the user can see "what we were just talking about" after a refresh, not lose the thread. ParseCards re-rendering as plain text is acceptable.

### 4. Conversation list / switching

`DESIGN.md §6.2`'s "left drawer (mobile: top dropdown)" listing prior conversations. Each row: title + relative timestamp.

- Backend: `GET /chat/conversations` returning `[{conversation_id, title, last_message_at}, ...]`. Title is derived: first 60 chars of the first user message in the conversation. New conversations have title `"new conversation"` until they have a user turn.
- Frontend: the existing `Sidebar.tsx` (desktop) and `BottomNav.tsx` (mobile) don't have a slot for this — add a small "history" surface (drawer on mobile, sidebar accordion on desktop) that lists conversations and switches the active one when tapped. Switching means: set `chatStore.conversationId`, clear `messages`, call the `GET /chat/messages` hydrator from §3.
- A "new chat" button (the `SquarePen` icon top-right of the chat page) already exists and already routes through `chatStore.newChat()`. Make sure switching back to it produces a fresh conversation_id on the next send.

### 5. Entry-moment insight bubble (depends on Day 13)

This is mostly waiting on Day 13 to make `POST /transactions/confirm` actually return a non-null `insight`. Today the field comes back null and we have nothing to render. When Day 13 lands:

- Build `EntryInsightBubble.tsx` (Lovable doesn't have one — net new component). One sentence, quiet AI-bubble styling, auto-fade-in, no buttons. Reuse the moss-on-elevated treatment from the existing `MessageBubble`.
- In `chatStore.commitDraft`, after the optimistic ledger insert, if the confirm response's `insight` is non-null, append an `AssistantTextMessage` below the now-committed parse card carrying the insight string. Mark it with a new `kind: "insight"` if you want to differentiate styling — or just style on `via: "entry_insight"` to stay within the existing `ChatMessage` union.
- Spec is firm: never a modal, never a toast. Inline chat bubble only. Don't render anything when `insight === null`.

### 6. Generative charts

`Chart.tsx` + `render_chart` agent tool — not built. Net new work.

- **Dependency**: add `recharts` to `frontend/package.json` (the Lovable import already pulled it in transitively via `embla-carousel-react`/other deps; verify it's present and unpin if it's hiding in `transitivePeerDeps`). SVG only, responsive to 375px.
- **Backend**: add `render_chart` to [app/agent/tools.py](app/agent/tools.py) — a typed tool whose input is a `ChartSpec` JSON `{type: line|bar|stacked_bar|donut, x: string, series: [{name: string, data: [number]}], y_label: string, title: string}`. The tool's `result` echoes the spec back verbatim — the data extraction happens in the agent's existing `get_*` tools, and `render_chart` is purely a transport for "the model decided to chart this." Wire it into the tool registry alongside the existing read tools.
- **Frontend**: `Chart.tsx` renders a Recharts `<ResponsiveContainer>` + the right primitive based on `spec.type`. In `chatStore._renderTurn`, when a `render_chart` tool_call appears, build an `AssistantChartMessage` carrying the spec; the existing `MiniBarChart` slot in `MessageRow` already handles `kind: "chart"` for the local-mock format — extend it to also dispatch to `Chart.tsx` when the message has a richer spec, OR add a new `kind: "rich-chart"`. Cleaner option: replace `MiniBarChart` outright and have one `Chart.tsx` that handles all four types.

### 7. Intent inference for `get_transactions`

`chatStore._renderTurn` currently hardcodes `intent: "edit"` on the `AssistantCandidatesMessage` it builds from a `get_transactions` tool call. That's the friendlier default, but when the user said "delete the lunch from last week" and the agent ran `get_transactions` to find candidates, the rows would benefit from a destructive selection style.

Cheap fix: a small regex check against `assistant_text` — if it matches `/delete|remove|drop/i` *and* mentions one of the rows, set `intent: "delete"`. Better fix: prompt the agent to attach an `intent` field to the `get_transactions` result so we don't lossy-infer. Either approach works for v1.

Don't over-engineer this — the existing flow lets the user open the edit sheet and tap delete from there, so getting the chip color wrong doesn't break anything.

### 8. Tests

The original Day 10 spec listed tests that didn't ship:

- `tests/frontend/ParseCard.test.tsx` — rendering for each kind, "looks right" calls the right confirm endpoint, "let me fix it" preserves unedited fields.
- `tests/frontend/CandidateList.test.tsx` — tap opens edit sheet with the right id.
- `tests/frontend/Chart.test.tsx` — chart specs for line / bar / stacked_bar / donut render at 375px.
- `tests/test_chart_spec.py` (backend) — "Chart my dining by week in March" produces a correct ChartSpec via `render_chart`.

Pick the framework that's actually present in the frontend (the Lovable scaffold has Vitest peer deps but no test runner configured). Add `vitest` + `@testing-library/react` + `jsdom` to the dev deps; one runner config; then write the tests.

## Don't

- Don't keep the frontend category list as-is "for now." The mismatch keeps producing 422s; it's not a polish item.
- Don't add a `/cards/confirm` or `/subscriptions/confirm` endpoint here. Day 14 / Day 19 own those.
- Don't introduce a Service-Worker cache for `/chat/messages`. Authenticated financial data never sits in the SW cache (DESIGN.md §10.1, privacy invariant). The chat history fetch is in-memory only.
- Don't render `chat_turn_trace` rows in the conversation list — those are wire-shape and contain tool_use / tool_result pairs the user shouldn't see. Read from `chat_messages` only.
- Don't ship `EntryInsightBubble` ahead of Day 13. The insight field is null until then; the component would render nothing every time, which makes regressions invisible.
- Don't roll your own chart library. SVG via Recharts.
- Don't expand the `ToolName` union in `lib/chat.ts` with a hardcoded backend-tool list — that union came from the Lovable mock and the `via` chip is intentionally a simplification, not a comprehensive mapping. If a tool deserves its own UI, render it explicitly.

## Done when

- Frontend `CATEGORIES` exactly mirrors backend `ALLOWED_CATEGORIES`. No hardcoded references to removed categories anywhere in the frontend.
- `coffee $5.5` → "looks right" → 200 on `POST /transactions/confirm` → optimistic row in the dashboard. No 422.
- Daily-cap 429 from `/chat/turn` flips the chat input to the frame-16 amber card; `newChat()` resets it.
- Refresh the chat page mid-conversation → previous user/assistant text bubbles re-appear from `GET /chat/messages`.
- Conversation list shows prior conversations; tapping one loads its history.
- Day 13 having landed (`insight` is non-null), `EntryInsightBubble` renders below a fresh transaction confirm.
- "chart my dining by week in March" → `render_chart` tool call → Recharts line chart at 375px.
- Deleting a candidate via "delete the lunch from last week" surfaces a destructive-styled tap target (intent inference working).
- Frontend Vitest suite green; `ParseCard` / `CandidateList` / `Chart` tests cover the happy paths in the spec.
- Backend `test_chart_spec.py` green.
