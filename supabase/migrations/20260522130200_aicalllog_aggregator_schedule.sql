-- aggregate-aicalllog pg_cron schedule — Day 24 (DESIGN.md §14.1, §14.3).
--
-- Schedules `aggregate_aicalllog()` (defined in 20260522130100) for
-- 04:15 UTC daily. Spaced after:
--   * autolog_subscriptions   at 04:00 UTC (20260518130200)
--   * prune_user_memory       at 03:00 UTC (20260518120000)
-- so cron.job_run_details contention is sequential, not simultaneous.
--
-- Unconditional. An earlier draft of this migration gated the schedule
-- on `current_setting('app.environment') = 'production'` to keep it
-- from firing in dev. The gate required `ALTER DATABASE postgres SET
-- "app.environment" = 'production'`, which Supabase Free tier denies
-- (only Pro and above can set custom GUCs at the database level).
-- The gate is dropped here for three reasons:
--   1. The aggregator's WHERE clause already filters to
--      `timestamp < now() - 90 days AND user_id IS NOT NULL` — a fresh
--      `supabase db reset` leaves nothing matching, so the job is a
--      no-op in dev.
--   2. `tests/test_aggregator.py` calls `aggregate_aicalllog()`
--      directly via the admin_client RPC, not via the cron schedule,
--      so test behavior is unaffected by whether the schedule is
--      registered.
--   3. The `clean_ai_call_log` test fixture wipes both ai_call_log
--      and ai_call_log_daily before every test, so a cron run that
--      happens to fire between tests cannot leave state that affects
--      assertions.
--
-- `cron.schedule` upserts on (jobname); rerunning this migration
-- replaces the existing schedule rather than creating a duplicate.

SELECT cron.schedule(
    'aggregate-aicalllog',
    '15 4 * * *',
    'SELECT aggregate_aicalllog();'
);
