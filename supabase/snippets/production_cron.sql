-- Production cron jobs — Day 19 (DESIGN.md §14.3)
--
-- This file is applied ONLY in production, via `supabase db push --file`
-- against the prod Supabase project. It is deliberately NOT in
-- supabase/migrations/ because pg_cron schedules would auto-install in
-- dev and CI test environments, where they'd burn API/DB resources
-- without doing useful work and could interfere with `tests/test_autolog.py`
-- which calls `autolog_subscriptions()` manually under a per-test seed.
--
-- Dev / test invokes `SELECT autolog_subscriptions();` directly when it
-- needs to exercise the cron path. Production schedules it once via this
-- file.
--
-- Re-applying this file is idempotent: cron.schedule with the same job
-- name updates the schedule in place rather than creating a duplicate.

SELECT cron.schedule(
    'autolog-subscriptions',
    '0 6 * * *',  -- daily at 06:00 UTC
    $$SELECT autolog_subscriptions();$$
);

-- Day 22 (DESIGN.md §7.10): weekly trim of the eval user's ai_call_log
-- rows. The eval harness writes ai_call_log rows under
-- `eval@tameru.internal` on every CI eval run (invariant 14); this keeps
-- the table from growing unbounded. Weekly is plenty — eval rows have no
-- analytic value past the run that produced them.
SELECT cron.schedule(
    'trim-eval-user-ai-call-log',
    '0 4 * * 0',  -- weekly, Sunday 04:00 UTC
    $$SELECT trim_eval_user_ai_call_log();$$
);
