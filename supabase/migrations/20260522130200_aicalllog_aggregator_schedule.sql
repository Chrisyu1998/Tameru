-- aggregate-aicalllog pg_cron schedule — Day 24 (DESIGN.md §14.1, §14.3).
--
-- Schedules `aggregate_aicalllog()` (defined in 20260522130100) for
-- 04:15 UTC daily. Spaced after:
--   * autolog_subscriptions   at 04:00 UTC (20260518130200)
--   * prune_user_memory       at 03:00 UTC (20260518120000)
-- so cron.job_run_details contention is sequential, not simultaneous.
--
-- Production-only gate: the schedule is only registered when the
-- postgres custom setting `app.environment` resolves to 'production'.
-- Local Supabase leaves it unset and the schedule no-ops, so a
-- developer running `supabase db reset` doesn't accidentally trigger
-- aggregation on a fixture set that still includes recent-but-old-
-- looking dates. Set the setting on the production project via
-- Supabase Dashboard → Database → Custom Postgres Config:
--   app.environment = 'production'
--
-- Idempotent: `cron.schedule` upserts on (jobname); rerunning the
-- migration replaces the existing schedule rather than creating a
-- duplicate.

DO $$
BEGIN
    IF current_setting('app.environment', true) = 'production' THEN
        PERFORM cron.schedule(
            'aggregate-aicalllog',
            '15 4 * * *',
            'SELECT aggregate_aicalllog();'
        );
    END IF;
END;
$$;
