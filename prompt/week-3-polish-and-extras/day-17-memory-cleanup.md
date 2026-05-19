# Day 17 — Memory time decay + capacity cap

## Goal

`user_memory` stays bounded by a nightly Postgres job: facts older than 90 days without reinforcement are deleted; users with more than 60 remaining facts are trimmed back to 60 by a recency × relevance score. No LLM calls during pruning. Distillation is allowed to overflow the cap; `LIMIT 60` in the chat renderer keeps the overflow invisible to the agent until the next sweep.

## Read first

- `DESIGN.md` §7.6 (memory cleanup rules — the formula and overflow contract).
- `prompt/week-3-polish-and-extras/day-16-cross-session-memory.md` (distillation flow, dedup unique index, `upsert_user_memory_fact` RPC, existing `PATCH /memory/{id}` reinforcement behavior — all already shipped).
- `app/agent/memory.py` — `render_user_memory` is the sole consumer of memory in the chat hot path; it already applies `LIMIT 60`.
- `app/routes/memory.py` — `PATCH /memory/{id}` already bumps `reinforced_at` on every patch.

## Deliverables

### Migration

`..._memory_cleanup_function.sql`:

- SQL function `prune_user_memory()`:
  - `SECURITY DEFINER`, `SET search_path = public`. Runs from `pg_cron` with no `auth.uid()` — must operate cross-user, so it joins the small set of sanctioned RLS-bypass paths (subscription auto-logger, ai_call_log rollup; CLAUDE.md invariants 1 and 14).
  - Wrap the per-user trim loop in `pg_try_advisory_xact_lock(hashtextextended(user_id::text, 0))` so a distillation upsert mid-sweep cannot race with the same user's prune. Skip the user if the lock isn't acquired; the next nightly run picks them up.
  - Step 1 — time decay: `DELETE FROM user_memory WHERE reinforced_at < now() - interval '90 days'`.
  - Step 2 — capacity trim: for each `user_id` with `count(*) > 60`, delete the rows ranked lowest by:
    ```sql
    ORDER BY (relevance_score / (1 + GREATEST(0, (now()::date - reinforced_at::date)) / 30.0)) ASC,
             reinforced_at ASC
    ```
    until 60 remain. Implement via a CTE: rank rows per user, delete where rank > 60. The `reinforced_at ASC` tiebreaker is required — `relevance_score` defaults to 0.5 and ties will be common.
  - Single SQL transaction. No `COMMIT` mid-function. Returning `void`.
- `SELECT cron.schedule('prune-memory', '0 3 * * *', 'SELECT prune_user_memory();');` — runs daily at 03:00 UTC. Tracked migration; same prod-and-dev posture as the existing `pg_cron` jobs (no env gating).

### Backend

Nothing required in `app/agent/memory.py` for the cap. Distillation upserts via the RPC and may push a user to 61 or 62 facts; the renderer's `LIMIT 60` (already in place since Day 16) hides the overflow until the next 03:00 sweep. The 24-hour worst-case window is bounded and benign.

### Frontend

`frontend/src/pages/memory.tsx` — extend `CapacityRow`:

- Below the existing "X / 60 facts" line, add a small muted line: *"oldest fact reinforced N days ago"*.
- Source `N` from the smallest `reinforced_at` across the rows already in `useLedger().memory`. No new API call. If `memory.length === 0`, omit the line.

### Tests

`tests/test_memory_cleanup.py`:

- **time decay (boundary)**: seed one fact at `now() - interval '91 days'` and one at `now() - interval '89 days'`, both with `relevance_score = 1.0` so the capacity trim cannot also be the cause of a deletion. Run `SELECT prune_user_memory()`. Assert the 91-day row is gone and the 89-day row remains.
- **capacity trim, 100 → 60**: seed 100 facts with controlled `relevance_score` and `reinforced_at` chosen so the recency × relevance ordering picks an unambiguous bottom 40 (e.g. 60 keepers at relevance 0.9 / reinforced now, 40 drops at relevance 0.1 / reinforced 60 days ago). Run prune. Assert exactly 60 keepers remain. Matches Day 17's literal "100 seeded facts" done-when.
- **tie tiebreaker**: seed two facts whose recency × relevance scores are equal but with different `reinforced_at` values (use the formula's algebra to pick a tied pair, e.g. `(relevance=0.4, days=0)` vs `(relevance=0.6, days=15)` both score 0.4). Assert the older-reinforced one is deleted.
- **overflow tolerated by render**: seed 62 facts for one user; do NOT call prune. Call `render_user_memory(user_jwt)`. Assert the returned block contains exactly 60 `- [category] ...` lines.

The advisory-lock isolation case is intentionally omitted — verifying `pg_try_advisory_xact_lock` requires a second open Postgres connection outside the supabase-py / PostgREST path (no psycopg in deps). The lock primitive is well-understood and the failure mode is a single `CONTINUE`; the test docstring records the rationale.

## Don't

- Don't call an LLM during pruning. `relevance_score` was set at distillation time; re-scoring duplicates that work.
- Don't add an inline cap-check inside `distill_session`. Overflow is allowed; the renderer hides it. Adding a Haiku tiebreaker would re-litigate scores already assigned and add cost+latency per distillation at the cap.
- Don't gate the cron on environment. Same posture as the subscription auto-logger and ai_call_log rollup.
- Don't change the chat renderer's order. `render_user_memory` keeps its lex sort (`relevance DESC, reinforced_at DESC`) — the prune ranking is a slightly different function by design (DESIGN.md §7.6 notes the tolerated inconsistency).
- Don't decay facts the user has manually edited. `PATCH /memory/{id}` already bumps `reinforced_at` (Day 16, `app/routes/memory.py`). No change needed — just verify the test seeding doesn't rely on a stale `reinforced_at` after a patch.
- Don't issue per-row `DELETE` statements from Python. The whole sweep is one SQL function call.

## Done when

- `SELECT prune_user_memory()` on a user with 100 seeded facts leaves exactly 60, chosen by the recency × relevance formula.
- A 91-day-stale fact is gone after a sweep; an 89-day-stale fact remains.
- A user at 60 facts whose distillation adds 2 more new facts ends up at 62 immediately, with `render_user_memory` still returning exactly 60 lines. The next 03:00 sweep trims back to 60.
- `frontend/src/pages/memory.tsx` shows "oldest fact reinforced N days ago" beneath the capacity row when at least one fact exists.
- All five `tests/test_memory_cleanup.py` cases pass.
