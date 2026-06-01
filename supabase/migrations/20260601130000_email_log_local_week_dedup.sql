-- email_log local-week idempotency — Day 29 (DESIGN.md §6.4, §6.6).
--
-- Fixes a duplicate-send bug exposed by the Monday 09:00–noon retry window
-- (§6.6). The prior dedup keyed on `date_trunc('week', sent_at AT TIME ZONE
-- 'UTC')` — the UTC week of the SEND. For any zone east of UTC+9 (e.g.
-- Australia/Sydney, which is in the Settings picker), Monday 09:00 local is
-- Sunday 23:00 UTC — the *previous* UTC week — while the 10:00/11:00 retry
-- fires are Monday UTC. The three fires therefore received different
-- UTC-week keys, so a Sydney user could be sent two digests on one Monday.
--
-- The correct key is the user's LOCAL Monday date: invariant across all
-- three retry fires AND across a mid-week timezone change. A per-row tz
-- can't live in an IMMUTABLE index expression (memory.md 2026-05-25
-- date_trunc note), so the cron computes the local Monday date in Python
-- and passes it in; we store it in `dedup_week` and dedup on the column.

ALTER TABLE email_log
    ADD COLUMN dedup_week date;

COMMENT ON COLUMN email_log.dedup_week IS
    'Day 29 — the local Monday date this email is sent FOR, computed by the '
    'cron in the recipient''s timezone. The idempotency key: one success per '
    '(user, kind, local-week). NULL on pre-Day-29 rows. DESIGN.md §6.4, §6.6.';

-- Swap the UTC-week index for a local-week one. Partial on success (a failed
-- send leaves a success=false row that does NOT lock the week) AND on
-- dedup_week IS NOT NULL (legacy rows without a key never collide).
DROP INDEX IF EXISTS email_log_weekly_dedup;

CREATE UNIQUE INDEX email_log_dedup_week_uniq
    ON email_log (user_id, kind, dedup_week)
    WHERE success AND dedup_week IS NOT NULL;

-- Replace the idempotent-insert RPC with one that takes the local-week date.
-- The arg list changes, so DROP then CREATE (CREATE OR REPLACE would leave
-- the old 5-arg overload in place). Re-apply the memory.md 2026-05-18
-- REVOKE/GRANT privilege pattern for the new signature.
DROP FUNCTION IF EXISTS email_log_insert_idempotent(uuid, text, boolean, text, text);

CREATE OR REPLACE FUNCTION email_log_insert_idempotent(
    p_user_id             uuid,
    p_kind                text,
    p_success             boolean,
    p_provider_message_id text,
    p_error_code          text,
    p_dedup_week          date
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
        error_code,
        dedup_week
    )
    VALUES (
        p_user_id,
        p_kind,
        p_success,
        p_provider_message_id,
        p_error_code,
        p_dedup_week
    )
    ON CONFLICT (user_id, kind, dedup_week) WHERE success AND dedup_week IS NOT NULL
    DO NOTHING
    RETURNING *;
END;
$$;

REVOKE EXECUTE ON FUNCTION email_log_insert_idempotent(uuid, text, boolean, text, text, date)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION email_log_insert_idempotent(uuid, text, boolean, text, text, date)
    TO service_role;

COMMENT ON FUNCTION email_log_insert_idempotent(uuid, text, boolean, text, text, date) IS
    'Day 29 — INSERT into email_log with ON CONFLICT DO NOTHING against the '
    'partial unique index email_log_dedup_week_uniq (one success per user/kind/'
    'local-week; key passed as p_dedup_week, the recipient-local Monday date). '
    'Empty SETOF return means a successful send already exists for the week; '
    'one row means freshly inserted. SECURITY DEFINER + REVOKE/GRANT to '
    'service_role is the memory.md 2026-05-18 privilege pattern. DESIGN.md '
    '§6.4, §6.6.';
