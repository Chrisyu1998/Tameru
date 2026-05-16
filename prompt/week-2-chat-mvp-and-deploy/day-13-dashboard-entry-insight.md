# Day 13 — Minimal dashboard + baseline calc + entry-moment insight (inline in confirm response)

## Goal

Single-screen dashboard (UX frame 8): headline number + 4–5 category tiles showing **delta vs baseline**. After every successful transaction confirm, surface one contextual sentence as a **chat bubble** below the just-committed transaction (UX frame 15's second AI bubble). The insight is a quiet reflection, not a toast — because the write flow now lives in chat (CLAUDE.md invariant 8), not a separate entry form.

## Read first

- `DESIGN.md` §6.2 (dashboard philosophy + chat-based entry flow + Entry-Moment Insight rules), §6.3 (baselines).
- `UX_PROMPT.md` frames 8 (Home Default), 9 (Home Empty), 15 (Transaction Confirmation with the insight bubble).
- The Lovable scaffold at `frontend/src/pages/home.tsx`. It already renders the right visual shape with hardcoded fixture baselines (`CATEGORY_BASELINES`, `TOTAL_BASELINE`) and a TS-side `buildObservation` helper. **This file is the dashboard surface — do not create a new `Dashboard.tsx`.** Day 13's frontend work is rewiring `home.tsx` to the backend and deleting the fixture-driven dead code, not building a new page.

## Deliverables

### Backend — dashboard

`app/routes/dashboard.py`:

- `GET /dashboard/summary` → `{ this_month, baseline, delta_pct, baseline_ready: bool, observation: str | null, categories: [{ name, this_month, baseline, delta_abs, delta_pct, color: green|neutral|amber|red, baseline_ready: bool }] }`.
- Baseline = trailing 3-month average per category (and total), computed from `transactions` via the user's JWT. Months are bucketed in the user's home timezone (DESIGN.md §6.3), not UTC — a transaction at 11:59 PM local on the 31st belongs to that month, not the next.
- **`observation` is the one-sentence prose summary the dashboard renders below the headline** (e.g., "dining is doing most of the lifting this month."). Generated server-side from the same baseline state the tiles use. The existing TS-side `buildObservation` in `home.tsx` is deleted as part of this prompt — prose generation lives in one place, in Python, alongside the data it reads.
- **New-user gate, softened from the original 3-month hard cutoff.** Per-category `baseline_ready: true` iff that category has ≥6 historical transactions **and** ≥30 days of history before the current month. Until both fire, the tile renders with a "still learning" badge and `baseline=null`, `delta_abs=null`, `delta_pct=null`. The top-level `baseline_ready` flips true once any category clears the gate. A user with truly zero history → top-level `baseline_ready: false`, all categories empty, and `observation` returns a "keep logging" hint string.
- Rationale for the softer gate: the v1 audience is ~10 friends and family. A 3-month dead-app feels worse than a tentative-but-honest signal at week 2. Statistical purity isn't worth the product cost at this cohort size.

### Backend — entry-moment insight (inline, not a separate endpoint)

The insight is computed inside `POST /transactions/confirm` (Day 5) and returned in the same response as the committed transaction: `{ transaction, insight: str | null }`. It is **not** a separate `/insights/entry_moment` endpoint — that was the old model where the entry form fired a second call after save.

`app/services/entry_moment.py`:

- `def entry_moment_insight(user_jwt, transaction) -> str | None:` — **deterministic, non-LLM function** (no Anthropic / Gemini / Perplexity calls). It is **not pure**: it reads from `transactions`, `cards`, `entry_moment_fires`, and the user's baseline state via the user's JWT (RLS-scoped). "Deterministic" means no model variance, not no I/O.
- **Single-RPC candidate evaluation.** Read all four candidate signals in **one** Supabase RPC call — a SQL function added in this prompt's migration named `entry_moment_signals(p_transaction_id uuid)`, `SECURITY INVOKER` so RLS still scopes reads to the caller. It returns one row containing: weekly-count-in-category, monthly-cumulative-delta, single-transaction-extreme flag, card-multiplier-mismatch context, and the latest fire timestamp per rule from `entry_moment_fires`. **Do not** stack 4 sequential Supabase queries from Python — that blows the latency budget.
- **Rules, priority order (highest first), with rationale.** The order weights *useful-once* over *useful-repeatedly* so a user doesn't see the same nag every confirm:
  1. **Single-transaction notable** ("highest single dining spend this month.") — naturally rare; fires at most once per category per month.
  2. **Above-average frequency for the week** ("4th dining transaction this week — you usually have 2.") — fires at most once per category per week.
  3. **Cumulative delta vs baseline** ("this puts you $23 above your monthly dining average with 12 days left.") — fires at most once per category per week, and is suppressed when rule 2 already fired in that category this week (they answer adjacent questions).
  4. **Card mismatch** ("you've used Chase Freedom for dining 3 times this week — Amex Gold earns 4x there.") — fires at most **once per 14 days across all categories**. Lowest priority because it's the most fatiguing if uncapped, and the action it suggests (switch cards) doesn't change daily.
- Rate-limit state lives in a new `entry_moment_fires` table (migration in this prompt): `(user_id, rule_id, category, fired_at)` with index on `(user_id, fired_at desc)`. RLS: same shape as `transactions` — `auth.uid() = user_id` on select/insert, no update/delete policy (audit-immutable, same posture as `ai_call_log` per invariant 14). The RPC reads this table; the confirm endpoint inserts a row after a non-null insight is returned.
- **Noise threshold combines percent and absolute floor.** A rule fires only when `abs(delta) > 10% of baseline` **AND** `abs(delta) > $10` (configurable as `ENTRY_MOMENT_MIN_DELTA_USD` env var, default 10). Percent alone misweights small-baseline categories — 10% of a $50 groceries baseline is $5, which is noise.
- Returns `None` when nothing meaningful applies (first transaction in a category, within-noise delta, all applicable rate limits saturated, category still under the new-user soft gate). The confirm endpoint passes `None` through to the client, which skips the insight bubble entirely.
- **On idempotent replay** (a `POST /transactions/confirm` that matched an existing row by `client_request_id` — see Day 5), the confirm endpoint returns `insight: null` without calling `entry_moment_insight()` and without writing to `entry_moment_fires`. The user either already saw the insight when the original confirm landed, or has long since moved past it — re-firing stale context is worse than silence.
- **No insight for pg_cron auto-logged rows.** The subscription auto-logger writes via the service role from a `pg_cron` SQL function — `entry_moment_insight` is never called from that path. When the user opens the app and sees a freshly-auto-logged Netflix row, no bubble appears. Insights are tied to the confirm action, not to a row's existence.
- **Latency budget: p95 ≤ 250ms** for the insight computation portion (one RPC + one insert), measured from inside `confirm_transaction` from just before `entry_moment_insight()` to just after. The total confirm-endpoint p95 budget stays under 500ms with seeded data. The original prompt's 150ms target was aspirational and assumed a no-I/O pure function; the consolidated RPC makes 250ms honest and still well below human-perceivable-as-laggy.

### Frontend — dashboard

Rewire `frontend/src/pages/home.tsx`:

- Replace the imports of `CATEGORY_BASELINES` / `TOTAL_BASELINE` from `@/lib/fixtures` with a `useDashboardSummary()` hook backed by TanStack Query that calls `GET /dashboard/summary` (60s stale time; invalidate on `ledger.refresh`). **Delete `CATEGORY_BASELINES`, `TOTAL_BASELINE`, and `buildObservation`** — they are dead once the backend lands. The fixture file keeps only the seeds onboarding-tour and tests still reference.
- Render `observation` from the backend response in place of the inline `buildObservation` call. Italic serif treatment unchanged.
- Each tile shows category name + delta as **absolute dollars only** ("Dining: +$47 above average"). When that tile's `baseline_ready` is false, render the "still learning" badge in the slot where the delta would go. Do not show the absolute month spend on tiles.
- Headline color logic stays in TS but consumes the backend `delta_pct` field, not a frontend-computed one.
- Empty state (UX frame 9): ledger icon + "your ledger is empty" + subtitle pointing to the chat button in the bottom nav + downward arrow + subtle pulse ring on the chat button. Already implemented as `EmptyHome` — leave the visual code intact; just confirm it renders when the backend response has top-level `baseline_ready: false` AND `this_month === 0`. **No "Ask me about your spending" placeholder link** — the bottom-nav chat button is the always-visible CTA.
- Must fit on one screen at a **375 × 667** viewport (iPhone SE, 375 wide × 667 tall). **No scrolling.** If it overflows, drop a tile.

### Frontend — entry-moment insight (chat bubble, not toast)

The insight renders as a quiet AI bubble below the just-committed transaction via a new `EntryInsightBubble` component. It consumes the `insight` field returned by `POST /transactions/confirm`. No `EntryInsightToast.tsx` — the toast pattern is gone now that the write flow is in chat.

> **Carried over from Day 10b §5.** The Day 10b prompt deferred this component because the `insight` field was returning null until Day 13's backend rule set landed. Now that the rules are shipping in this prompt, `EntryInsightBubble` is a Day 13 deliverable — do not skip it.

Build details:

- **Fix the API wrapper first.** `frontend/src/lib/transactionsApi.ts:113-126` currently discards `wire.insight` and returns only `fromWire(wire.transaction)`. Change `confirmTransaction` to return `{ transaction: Transaction, insight: string | null }`. Update both call sites — there is one in `chatStore.commitDraft`.
- New component at `frontend/src/components/chat/EntryInsightBubble.tsx`. Net new — Lovable scaffold has nothing like it. One sentence, quiet AI-bubble styling, auto-fade-in, no buttons, no dismiss affordance (rate limits do the fatigue control server-side; a per-bubble × would invite churn). Reuse the moss-on-elevated treatment from `MessageBubble` (`bubble={true}`, `role="assistant"`, no `via` chip).
- In `chatStore.commitDraft`, after the optimistic ledger insert, if the response includes a non-null `insight`, append an `AssistantTextMessage` (or a new `kind: "insight"` if you want differentiated styling — `via: "entry_insight"` works inside the existing union too). The append must happen **after** the parse card flips to committed state so the bubble lands below it visually.
- Spec is firm: never a modal, never a toast. Inline chat bubble only. Don't render anything when `insight === null` — that includes the idempotent-replay case (the backend returns null there by contract, so no client-side branching needed).

### Tests

- `tests/test_baselines.py`: 3-month rolling avg with gaps in months, single-day-spread data, the new soft new-user path at the ≥6-tx-and-≥30-days boundary (test both sides of each leg), timezone boundary — a transaction at 23:59 in the user's home tz must land in the user's local month, not UTC's. Category spent historically but not this month → `delta = -baseline` (not zero, not omitted from the tile list).
- `tests/test_entry_moment.py`: the right rule fires for synthetic histories; `None` when nothing applies; priority order respected when multiple rules match; rate limits suppress within-window re-fires across requests; the percent-and-absolute-floor threshold rejects a synthetic delta that passes 10% but fails the $10 floor (and the inverse); `entry_moment_fires` gets a row on every non-null fire and no row on `None`.
- `tests/routes/test_transactions.py` (extending Day 5): `POST /transactions/confirm` returns `{ transaction, insight }` with `insight` populated or null per the rules. On a replayed confirm (same `client_request_id`), `insight` is always `null` even when the rule set would otherwise fire, **and** `entry_moment_fires` is not written.
- `tests/routes/test_dashboard.py` (new): documented response shape; per-category soft-gate categories render `baseline_ready: false`; truly-zero-history user gets top-level `baseline_ready: false` and a "keep logging" `observation`; tz-boundary transactions land in the right month bucket.
- `frontend/tests/EntryInsightBubble.test.tsx`: renders the sentence when `insight` is non-null; renders nothing when `insight` is null. Extend `frontend/tests/ParseCard.test.tsx` (or add a chatStore-level test) to verify a successful `commitDraft` with a non-null insight appends exactly one extra message, and that a null insight appends zero.

## Don't

- Don't add a 6-month bar chart. Don't add tabs. Don't add a "drilling" interaction on the dashboard. The chat is the escape valve; the per-category list (Day 15) is the history surface.
- Don't show absolute totals on tiles. Delta only.
- Don't make the entry-moment insight an AI call. Deterministic rules for v1.
- Don't expose the insight as a standalone endpoint. It travels with the transaction confirm response.
- Don't render the insight as a toast. Inline chat bubble only.
- Don't add a per-bubble dismiss × — rate limits handle fatigue server-side.
- Don't add the "Ask me about your spending" placeholder — the bottom-nav chat button is the CTA.
- Don't stack 4 sequential Supabase queries in `entry_moment_insight`. One RPC.
- Don't keep `CATEGORY_BASELINES`, `TOTAL_BASELINE`, or `buildObservation` after this prompt — they are scaffold dead code once the backend lands.
- Don't fire an insight on pg_cron auto-logged subscription rows.
- Don't create a new `Dashboard.tsx`. `home.tsx` is the dashboard surface; rewire it in place.

## Done when

- Dashboard fits one screen at 375 × 667 with no scrolling, sourced from `/dashboard/summary`.
- `CATEGORY_BASELINES`, `TOTAL_BASELINE`, and `buildObservation` are deleted from the frontend.
- `POST /transactions/confirm` returns three different insight sentences for three transactions in three different categories, matching the deterministic rule set and priority order.
- `POST /transactions/confirm` returns `insight: null` for: a first-transaction-in-category case, a within-noise delta, an idempotent replay, and a saturated rate-limit case.
- `entry_moment_fires` accumulates one row per non-null insight and zero rows on replays.
- Baseline calc returns sensible numbers; the per-category `baseline_ready: false` path renders the "still learning" badge; the truly-empty-history user gets the top-level "keep logging" path.
- After tapping "looks right" on a parse card whose confirm response includes an `insight`, an `EntryInsightBubble` appears beneath the committed card with that sentence. When `insight` is null, no bubble appears.
- p95 of the `entry_moment_insight` portion of confirm stays under 250ms in local benchmark with seeded data; total confirm-endpoint p95 stays under 500ms.
