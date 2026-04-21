# Day 13 — Minimal dashboard + baseline calc + entry-moment insight

## Goal

Single-screen dashboard: headline number + 4–5 category tiles showing **delta vs baseline**. After every successful transaction save, replace the toast with one contextual sentence.

## Read first

- `DESIGN.md` §6.2 (dashboard philosophy — fits on one screen, no scrolling), §6.3 (baselines), the entry-moment insight rules.

## Deliverables

- Backend:
  - `app/routes/dashboard.py`:
    - `GET /dashboard/summary` → `{this_month: number, baseline: number, delta_pct: number, categories: [{name, this_month, baseline, delta_abs, delta_pct, color: green|neutral|amber|red}]}`.
    - Baseline = trailing 3-month average per category (and total). New users (<3 months of data) → `{baseline_ready: false}` and the response includes a "Keep logging" message.
  - `app/routes/insights.py`:
    - `POST /insights/entry_moment` — body: `{transaction_id}`. Returns `{insight: "..." | null}`. The function picks **one** of these patterns based on the just-logged transaction (see DESIGN.md §6.2 list; pick the first that applies in priority order):
      1. Above-average frequency for the week ("4th dining transaction this week — you usually have 2.")
      2. Cumulative delta vs baseline ("This puts you $23 above your monthly dining average with 12 days left.")
      3. Single-transaction notable ("Highest single dining spend this month.")
      4. Card mismatch ("You've used Chase Freedom for dining 3 times this week — Amex Gold earns 4x there.")
    - Returns `null` when nothing meaningful applies (e.g., first transaction in a category). Today, this is a **deterministic Python function** — no AI call. We can swap to AI later if rules feel stale.
- Frontend:
  - `frontend/src/components/Dashboard.tsx`:
    - Headline: this month's total + delta_pct vs baseline. Color the number per the design.
    - 4–5 category tiles (top categories by delta magnitude). Each shows category name + delta as **absolute dollars only** ("Dining: +$47 above average"). Not the absolute total.
    - One "Ask me about your spending" prompt (links to `/chat`, built Day 18; for now just a placeholder route).
    - Must fit on one screen at 375px-tall viewport. **No scrolling.** If it overflows, drop a tile.
  - `frontend/src/components/EntryInsightToast.tsx`:
    - Replaces the standard "saved" toast on transaction save.
    - One sentence. Auto-dismiss at 3 seconds. No buttons.
    - Calls `POST /insights/entry_moment` with the new transaction's id; renders the returned sentence. If `null`, falls back to a quiet "Saved" with no fanfare.
- Tests:
  - `tests/test_baselines.py`: assert correct 3-month rolling avg with edge cases (gaps in months, single-day-spread data).
  - `tests/test_entry_moment.py`: assert the right rule fires for synthetic transaction histories.

## Don't

- Don't add a 6-month bar chart. Don't add tabs. Don't add a "drilling" interaction. The chat is the escape valve — that's Day 18.
- Don't show absolute totals on tiles. Delta only.
- Don't make the entry-moment insight an AI call. Deterministic rules.

## Done when

- Dashboard fits one screen on a 375×667 viewport with no scrolling.
- Logging a transaction in 3 different categories produces 3 different entry-moment sentences matching the rule set.
- Baseline calc returns sensible numbers and the "keep logging" message for new users.
