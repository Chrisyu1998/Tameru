# Day 18 — Chat UI (full): thread, write flows, candidate cards, charts

## Goal

The full chat experience. Chat is the only user-initiated write surface in v1 (CLAUDE.md invariant 8), so this day covers more than Q&A — it renders the **parse card** (UX frame 15) for `propose_*` tool results, the **candidate list** when `get_transactions` returns multiple rows, and the **entry-moment insight bubble** after a confirm. Plus the original read-path features: streaming, tool_use indicator, inline Recharts for generative charts.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow), §7.1–7.2 (agent loop + tools + propose-then-confirm), §7.8 (generative charts).
- `UX_PROMPT.md` frames 12–16.
- `CLAUDE.md` invariants 2, 8.

## Deliverables

### Chat thread and input

`frontend/src/pages/Chat.tsx`:

- Conversation thread (oldest top, newest bottom). Auto-scroll on new content.
- Input bar at bottom (multiline textarea; submit on Cmd/Ctrl+Enter; tap-to-send button). Mic button integration belongs to Day 21; this day wires a placeholder slot.
- Streaming render: tokens arrive via SSE (Day 17), append to the in-flight assistant bubble.
- Tool-use indicator: while a `tool_use` event is active, show a small quiet pill in the bubble (e.g. "looking up dining transactions…"). Replace with the next text chunk when it arrives.
- Conversation list: left drawer (mobile: top dropdown) showing prior conversations by title. New-conversation button.

`frontend/src/components/ChatThread.tsx`: reusable, also used by the guided tour (Day 10).

### Inline cards: write flows (parse card, candidate list)

This is the core addition that makes chat a complete write surface.

`frontend/src/components/ParseCard.tsx`:

- Rendered when a `tool_result` for `propose_transaction`, `propose_card`, or `propose_subscription` arrives. Distinguishes the three by `kind` on the payload.
- For `transaction` kind (UX frame 15): five rows (merchant / amount / date / card / category) each with a pencil glyph, editable inline. Action row: "let me fix it" secondary + "looks right" primary.
- For `card` kind: network · last-4 · program · multipliers (as multiplier chips) · annual fee · source URLs as citation links. Same "looks right" / "let me fix it" buttons.
- For `subscription` kind: name · amount · frequency · next billing · card · category. Same button pair.
- "Looks right" → calls the matching `POST /<resource>/confirm` endpoint (Day 5 / Day 11 / Day 14) with the proposal. On success, the returned entity (and, for transactions, the entry-moment insight) is rendered into the chat.
- "Let me fix it" → inline edit of the card fields; "looks right" re-enables after edits.
- **Nothing is written to the database from this component except via the confirm endpoints.** A parse card in the chat is a preview, never a row.

`frontend/src/components/CandidateList.tsx`:

- Rendered when a `tool_result` for `get_transactions` returns multiple rows and Claude's surrounding prose suggests disambiguation.
- Each row: date · merchant · amount · card last-4 — compact, tappable. Tapping a row opens `EditTransactionSheet` (Day 12) for that transaction.
- The component is dumb: it renders whatever the tool result contained. Claude's prose ("I see three coffees around that time, tap one to edit") supplies the framing.

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

When the SSE stream emits an `error` event with `code: "DAILY_CAP_EXCEEDED"` (Day 16, DESIGN.md §11.2), render the inline frame-16 treatment: amber-tinted card replacing the input row with "you've used your daily ai quota" title and "resets at midnight utc" subtitle. No retry button. Other error codes get Day 17's generic "connection lost — retry" handling.

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
- "change that $10 coffee from last week" (with seeded ambiguity) → Claude's prose plus a candidate list → tapping a candidate opens the edit sheet (Day 12).
- "add a card called Chase Sapphire Preferred" → parse card in `card` kind → "looks right" → card created.
- "chart my grocery spending by week in March" → inline line chart via `render_chart`.
- Token streaming feels smooth (chunks appear without perceptible jitter).
- Switching conversations preserves their state.
- Hitting the daily cap during a turn renders the frame-16 treatment and freezes further sends until reset.
