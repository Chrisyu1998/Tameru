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
- **On idempotent replay** (a `POST /transactions/confirm` that matched an existing row by `client_request_id` — see Day 5), the confirm endpoint returns `insight: null` without calling `entry_moment_insight()` at all. The user either already saw the insight when the original confirm landed, or has long since moved past the conversation; re-firing stale context is worse than silence (Day 15 offline queue note).
- **Deterministic Python — no AI call.** Keeps latency in the confirm response under ~150ms so the bubble appears fluidly after the parse-card "looks right" tap. Swapping to Haiku-generated prose is a post-launch experiment, not v1 scope.

### Frontend — dashboard

`frontend/src/components/Dashboard.tsx`:

- Top bar: "home" (Fraunces lowercase) left · "↗ Breakdown" quiet accent link right (UX frame 8).
- Headline: this month's total + delta_pct vs baseline. Color per design.
- 4–5 category tiles (top categories by delta magnitude). Each shows category name + delta as **absolute dollars only** ("Dining: +$47 above average"). Not the absolute total.
- Empty state (UX frame 9): ledger icon + "your ledger is empty" + subtitle pointing to the chat button in the bottom nav + downward arrow + subtle pulse ring on the chat button. **No "Ask me about your spending" placeholder link** — the chat button in the bottom nav is the always-visible CTA, and duplicating it on the dashboard adds noise.
- Must fit on one screen at 375px-tall viewport. **No scrolling.** If it overflows, drop a tile.

### Frontend — entry-moment insight (chat bubble, not toast)

The insight is rendered as a quiet AI bubble below the just-committed transaction by the `EntryInsightBubble` component. It consumes the `insight` field returned by `POST /transactions/confirm`. No `EntryInsightToast.tsx` — the toast pattern is gone now that the write flow is in chat.

> **Carried over from Day 10b §5.** The Day 10b prompt deferred this component because the `insight` field was returning null until Day 13's backend rule set landed. Now that the rules are shipping in this prompt, `EntryInsightBubble` is a Day 13 deliverable — do not skip it.

Build details (mirrors Day 10b §5 spec so you don't have to bounce between files):

- New component at `frontend/src/components/chat/EntryInsightBubble.tsx`. Lovable scaffold has nothing like it — net new. One sentence, quiet AI-bubble styling, auto-fade-in, no buttons. Reuse the moss-on-elevated treatment from the existing `MessageBubble` (`bubble={true}`, `role="assistant"`, no `via` chip).
- In `chatStore.commitDraft`, after the optimistic ledger insert, if `confirmTransaction()`'s response includes a non-null `insight`, append an `AssistantTextMessage` (or a new `kind: "insight"` if you want to differentiate styling — `via: "entry_insight"` works inside the existing union too). The append must happen *after* the parse card flips to committed state so the bubble lands below it visually.
- Spec is firm: never a modal, never a toast. Inline chat bubble only. Don't render anything when `insight === null` — that includes the idempotent-replay case (the backend returns null there by contract, so no client-side branching needed).
- Tests in `frontend/tests/EntryInsightBubble.test.tsx`: renders the sentence when `insight` is non-null; renders nothing when `insight` is null. Wire `tests/frontend/ParseCard.test.tsx` (or add a chatStore-level test) to verify a successful `commitDraft` with a non-null insight appends exactly one extra message and that a null insight appends zero.

The Day 10b prompt's `Don't` list still applies here: do not render `chat_turn_trace` rows into the insight bubble; the field originates from `/transactions/confirm`, not the chat history fetch.

### Tests

- `tests/test_baselines.py`: correct 3-month rolling avg with edge cases (gaps in months, single-day-spread data, new-user path).
- `tests/test_entry_moment.py`: the right rule fires for synthetic transaction histories; `None` when nothing applies; priority order is respected when multiple rules match.
- `tests/test_transactions.py` (extending Day 5): `POST /transactions/confirm` returns `{transaction, insight}` with `insight` populated or null per the rules. On a replayed confirm (same `client_request_id`), `insight` is always `null` even when the rule set would otherwise fire.

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
- After tapping "looks right" on a parse card whose confirm response includes an `insight`, an `EntryInsightBubble` appears beneath the committed card with that sentence. When `insight` is null, no bubble appears.
