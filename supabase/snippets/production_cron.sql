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
