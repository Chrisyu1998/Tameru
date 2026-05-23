-- email_log_insert_idempotent(...) — Day 25 (DESIGN.md §6.4, §8.14).
--
-- WHY THIS FUNCTION EXISTS
--
-- The digest cron writes one email_log row per send and needs ON CONFLICT
-- DO NOTHING against the partial unique index `email_log_weekly_dedup`
-- (... WHERE success). PostgREST's .upsert(on_conflict="cols") parameter
-- only accepts a column list; it cannot pass the partial-index WHERE
-- predicate. Without the matching predicate Postgres refuses the
-- inference with 42P10 "no unique or exclusion constraint matching the
-- ON CONFLICT specification" — same failure that bit the Day 20 CSV
-- import path (memory.md 2026-05-19).
--
-- This function emits `INSERT ... ON CONFLICT (user_id, kind,
-- date_trunc('week', sent_at)) WHERE success DO NOTHING` directly so
-- Postgres can use the partial index. Returns SETOF email_log: an empty
-- result tells the caller the conflict path fired (already sent this
-- week); a one-row result is the freshly-inserted record.
--
-- SECURITY POSTURE
--
-- SECURITY DEFINER + the REVOKE/GRANT pattern from memory.md 2026-05-18:
-- explicitly REVOKE EXECUTE from PUBLIC, anon, AND authenticated, then
-- GRANT to service_role only. The default-privileges backfill
-- (20260515210000) auto-grants EXECUTE to anon+authenticated at function
-- creation; REVOKE FROM PUBLIC alone does NOT dislodge those role
-- grants. Without this pair, any authenticated user could write
-- email_log rows attributed to any user_id (DEFINER bypasses RLS and
-- the function takes user_id as a parameter). That would be a hole.

CREATE OR REPLACE FUNCTION email_log_insert_idempotent(
    p_user_id             uuid,
    p_kind                text,
    p_success             boolean,
    p_provider_message_id text,
    p_error_code          text
)
    RETURNS SETOF email_log
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public
AS $$
BEGIN
    RETURN QUERY
    INSERT INTO email_log (
        user_id,
        kind,
        success,
        provider_message_id,
        error_code
    )
    VALUES (
        p_user_id,
        p_kind,
        p_success,
        p_provider_message_id,
        p_error_code
    )
    ON CONFLICT (user_id, kind, date_trunc('week', sent_at AT TIME ZONE 'UTC'))
        WHERE success
    DO NOTHING
    RETURNING *;
END;
$$;

REVOKE EXECUTE ON FUNCTION email_log_insert_idempotent(uuid, text, boolean, text, text)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION email_log_insert_idempotent(uuid, text, boolean, text, text)
    TO service_role;

COMMENT ON FUNCTION email_log_insert_idempotent(uuid, text, boolean, text, text) IS
    'Day 25 — INSERT into email_log with ON CONFLICT DO NOTHING against the '
    'partial unique index email_log_weekly_dedup. Empty SETOF return means '
    'a successful send already exists for the week; one row means freshly '
    'inserted. SECURITY DEFINER + REVOKE/GRANT to service_role is the '
    'memory.md 2026-05-18 privilege pattern. DESIGN.md §6.4, §8.14.';
