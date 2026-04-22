# Day 13 — Minimal dashboard + baseline calc + entry-moment insight (inline in confirm response)

## Goal

Single-screen dashboard (UX frame 8): headline number + 4–5 category tiles showing **delta vs baseline**. After every successful transaction confirm, surface one contextual sentence as a **chat bubble** below the just-committed transaction (UX frame 15's second AI bubble). The insight is a quiet reflection, not a toast — because the write flow now lives in chat (CLAUDE.md invariant 8), not a separate entry form.

## Read first

- `DESIGN.md` §6.2 (dashboard philosophy + chat-based entry flow + Entry-Moment Insight rules), §6.3 (baselines).
- `UX_PROMPT.md` frames 8 (Home Default), 9 (Home Empty), 15 (Transaction Confirmation with the insight bubble).

## Deliverables

### Backend — dashboard

`app/routes/dashboard.py`:

- `GET /dashboard/summary` → `{this_month, baseline, delta_pct, categories: [{name, this_month, baseline, delta_abs, delta_pct, color: green|neutral|amber|red}]}`.
- Baseline = trailing 3-month average per category (and total). New users (<3 months of data) → `{baseline_ready: false}` with a "keep logging" message.

### Backend — entry-moment insight (inline, not a separate endpoint)

The insight is computed inside `POST /transactions/confirm` (Day 5) and returned in the same response as the committed transaction: `{transaction, insight: str | null}`. It is **not** a separate `/insights/entry_moment` endpoint — that was the old model where the entry form fired a second call after save.

`app/services/entry_moment.py`:

- `def entry_moment_insight(user_jwt, transaction) -> str | None:` — pure function (no external calls) that picks **one** pattern based on the just-committed transaction, per `DESIGN.md` §6.2, priority order:
  1. Above-average frequency for the week ("4th dining transaction this week — you usually have 2.")
  2. Cumulative delta vs baseline ("this puts you $23 above your monthly dining average with 12 days left.")
  3. Single-transaction notable ("highest single dining spend this month.")
  4. Card mismatch ("you've used Chase Freedom for dining 3 times this week — Amex Gold earns 4x there.")
- Returns `None` when nothing meaningful applies (first transaction in a category, within-10%-of-baseline noise). Day 5's confirm endpoint passes the `None` through to the client, which then skips the insight bubble entirely.
- **Deterministic Python — no AI call.** Keeps latency in the confirm response under ~150ms so the bubble appears fluidly after the parse-card "looks right" tap. Swapping to Haiku-generated prose is a post-launch experiment, not v1 scope.

### Frontend — dashboard

`frontend/src/components/Dashboard.tsx`:

- Top bar: "home" (Fraunces lowercase) left · "↗ Breakdown" quiet accent link right (UX frame 8).
- Headline: this month's total + delta_pct vs baseline. Color per design.
- 4–5 category tiles (top categories by delta magnitude). Each shows category name + delta as **absolute dollars only** ("Dining: +$47 above average"). Not the absolute total.
- Empty state (UX frame 9): ledger icon + "your ledger is empty" + subtitle pointing to the chat button in the bottom nav + downward arrow + subtle pulse ring on the chat button. **No "Ask me about your spending" placeholder link** — the chat button in the bottom nav is the always-visible CTA, and duplicating it on the dashboard adds noise.
- Must fit on one screen at 375px-tall viewport. **No scrolling.** If it overflows, drop a tile.

### Frontend — entry-moment insight (chat bubble, not toast)

The insight is rendered as a quiet AI bubble below the just-committed transaction by the `EntryInsightBubble` component, which lives in Day 10's chat UI and consumes the `insight` field returned by `POST /transactions/confirm`. **This day ships only the backend** — Day 10 owns the rendering. No `EntryInsightToast.tsx` — the toast pattern is gone now that the write flow is in chat.

### Tests

- `tests/test_baselines.py`: correct 3-month rolling avg with edge cases (gaps in months, single-day-spread data, new-user path).
- `tests/test_entry_moment.py`: the right rule fires for synthetic transaction histories; `None` when nothing applies; priority order is respected when multiple rules match.
- `tests/test_transactions.py` (extending Day 5): `POST /transactions/confirm` returns `{transaction, insight}` with `insight` populated or null per the rules.

## Don't

- Don't add a 6-month bar chart. Don't add tabs. Don't add a "drilling" interaction on the dashboard. The chat is the escape valve; the per-category list (Day 15) is the history surface.
- Don't show absolute totals on tiles. Delta only.
- Don't make the entry-moment insight an AI call. Deterministic rules for v1.
- Don't expose the insight as a standalone endpoint. It travels with the transaction confirm response.
- Don't render the insight as a toast. It's an inline chat bubble (Day 10 owns the rendering).
- Don't add the "Ask me about your spending" placeholder — the bottom-nav chat button is the CTA.

## Done when

- Dashboard fits one screen on a 375×667 viewport with no scrolling.
- `POST /transactions/confirm` returns three different insight sentences for three transactions in three different categories, matching the deterministic rule set.
- `POST /transactions/confirm` returns `insight: null` for a first-transaction-in-category case.
- Baseline calc returns sensible numbers and the `baseline_ready: false` path for new users.
