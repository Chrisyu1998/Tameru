-- aggregate_aicalllog(): whole-day cutoff so a calendar date can never be
-- split across two runs (2026-06 audit P2-5).
--
-- The previous cutoff was the rolling `timestamp < now() - interval '90
-- days'`, evaluated at the fixed 04:15 UTC schedule time. For any calendar
-- date D that splits across the cutoff: run R aggregates only D's rows
-- with time-of-day < 04:15 and inserts a daily row keyed
-- (D, user, provider, model, task_type); run R+1 sees D's remaining rows
-- (>= 04:15, now past the cutoff), groups to the *same* key, hits the PK
-- conflict, `DO NOTHING` discards them — and the DELETE removed the raw
-- rows anyway. Every group whose rows straddled 04:15 UTC permanently lost
-- the post-04:15 portion (most US-daytime activity), silently
-- undercounting the long-horizon cost/audit record (invariant 15a).
--
-- Fix: truncate the cutoff to a UTC day boundary in both the INSERT and
-- the DELETE. Every calendar date is then either entirely inside or
-- entirely outside a given run's window: two runs on the same day use an
-- identical cutoff (idempotent), and the boundary only ever moves in
-- whole-day steps. `ON CONFLICT DO NOTHING` keeps covering the rare
-- INSERT-committed-but-DELETE-didn't double-fire window, which is the only
-- job it was meant to do.

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
    WHERE timestamp < date_trunc('day', now()) - interval '90 days'
      AND user_id IS NOT NULL
    GROUP BY date(timestamp), user_id, provider, model, task_type
    ON CONFLICT (date, user_id, provider, model, task_type) DO NOTHING;

    DELETE FROM ai_call_log
    WHERE timestamp < date_trunc('day', now()) - interval '90 days'
      AND user_id IS NOT NULL;
END;
$$;

-- CREATE OR REPLACE re-attaches the default-privilege grants from
-- 20260515210000, so the full three-role REVOKE is re-applied here
-- (memory.md 2026-05-18).
REVOKE EXECUTE ON FUNCTION aggregate_aicalllog() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION aggregate_aicalllog() TO service_role;
