# Day 18 — Chat UI + generative chart rendering

## Goal

Full chat experience: thread of messages, streaming token render, tool_use indicator while tools run, and Recharts rendered inline when Claude returns a chart spec.

## Read first

- `DESIGN.md` §6.2 (AI chat as the escape valve), §7.8 (generative charts).

## Deliverables

- Frontend:
  - `frontend/src/pages/Chat.tsx`:
    - Conversation thread (oldest top, newest bottom). Auto-scroll on new content.
    - Message input bar at the bottom (multiline textarea, submit on Cmd/Ctrl+Enter; tap-to-send button).
    - Streaming render: as tokens arrive, append to the in-flight assistant bubble.
    - Tool use indicator: while a `tool_use` event is active, show a small "🔧 Looking up your dining transactions…" pill in the bubble. Replace with the next text chunk when it arrives.
    - Conversation list: left drawer (or top dropdown on mobile) showing prior conversations by title. New conversation button.
  - `frontend/src/components/ChatThread.tsx`: reusable, also used by the guided tour (Day 10).
  - `frontend/src/components/Chart.tsx`:
    - Renders a Recharts component from a `ChartSpec` JSON: `{type: line|bar|stacked_bar|donut, x, series: [{name, data}], y_label, title}`.
    - SVG only. Responsive to 375px viewport.
- Backend:
  - Update the system prompt for the chat agent to include: "When the user asks for a chart, return a `<chart>{...ChartSpec JSON...}</chart>` block in your text response. The frontend will render it."
  - Or — preferred — add a typed `render_chart(spec)` tool the agent can call. The tool just echoes the spec back as a `tool_result`; the frontend extracts and renders it.
- Tests:
  - `tests/test_chart_spec.py`: assert the system prompt produces the right chart for "Chart my dining by week in March."

- **Daily cap UI.** When the SSE stream emits an `error` event with `code: "DAILY_CAP_EXCEEDED"` (defined Day 16, surfaced from §11.2 of DESIGN.md), render a friendly inline message in the chat: "You've used your daily AI quota — resets at midnight UTC." No retry button (retry won't help). Other error codes get the generic "Connection lost — Retry" treatment from Day 17.

## Don't

- Don't use Canvas-based chart libraries. SVG only (Recharts).
- Don't pre-compute charts on the backend. The agent decides; the frontend renders.
- Don't add chart export (PNG download). Out of scope for v1.

## Done when

- "Chart my grocery spending by week in March" produces a line chart inline in the chat.
- "Compare dining vs travel over the last 6 months" produces a grouped bar chart.
- Token streaming feels smooth (chunks appear without perceptible jitter).
- Switching conversations preserves their state.
