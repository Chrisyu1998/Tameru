-- aggregate_aicalllog() — Day 24 (DESIGN.md §8.9, §14.1, §14.5).
--
-- Rolls ai_call_log rows older than 90 days into ai_call_log_daily and
-- deletes the originals. Idempotent by construction: after the DELETE,
-- a re-run finds nothing to aggregate. `ON CONFLICT DO NOTHING` covers
-- the rare double-fire window where the INSERT wrote but the DELETE
-- hadn't yet (a follow-up run sees the rows-not-yet-deleted, but the
-- ON CONFLICT short-circuit makes the INSERT a no-op).
--
-- System-level rows (`user_id IS NULL`) are intentionally NEVER
-- aggregated (DESIGN.md §8.9 line 1034). ai_call_log_daily's composite
-- PK includes user_id, which cannot be NULL; a sentinel UUID would
-- break the auth.users FK. NULL-user rows therefore live in the raw
-- table past 90 days until a future system-level rollup table is added
-- if it ever becomes useful (not a v1 need).
--
-- SECURITY DEFINER + the REVOKE/GRANT pattern in memory.md
-- 2026-05-18: explicitly REVOKE EXECUTE from PUBLIC, anon, and
-- authenticated, then GRANT to service_role only. The default-
-- privileges backfill in 20260515210000 auto-grants EXECUTE to
-- anon+authenticated at function creation; REVOKE FROM PUBLIC alone
-- does NOT dislodge those role grants.

CREATE OR REPLACE FUNCTION aggregate_aicalllog()
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public
AS $$
BEGIN
    INSERT INTO ai_call_log_daily (
        date,
        user_id,
        provider,
        model,
        task_type,
        sum_input_tokens,
        sum_output_tokens,
        count,
        avg_latency_ms,
        error_count
    )
    SELECT
        date(timestamp)                                              AS date,
        user_id,
        provider,
        model,
        task_type,
        SUM(input_tokens)::bigint                                    AS sum_input_tokens,
        SUM(output_tokens)::bigint                                   AS sum_output_tokens,
        COUNT(*)::integer                                            AS count,
        AVG(latency_ms)::integer                                     AS avg_latency_ms,
        SUM(CASE WHEN success THEN 0 ELSE 1 END)::integer            AS error_count
    FROM ai_call_log
    WHERE timestamp < now() - interval '90 days'
      AND user_id IS NOT NULL
    GROUP BY date(timestamp), user_id, provider, model, task_type
    ON CONFLICT (date, user_id, provider, model, task_type) DO NOTHING;

    DELETE FROM ai_call_log
    WHERE timestamp < now() - interval '90 days'
      AND user_id IS NOT NULL;
END;
$$;

REVOKE EXECUTE ON FUNCTION aggregate_aicalllog() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION aggregate_aicalllog() TO service_role;
