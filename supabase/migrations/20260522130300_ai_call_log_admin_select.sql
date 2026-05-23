-- ai_call_log + ai_call_log_daily — admin SELECT widening — Day 24.
--
-- The existing SELECT policy is `USING (user_id = auth.uid())`, which
-- correctly scopes regular users to their own rows. But the admin
-- observability endpoint (`GET /admin/aicalls/summary` in
-- app/routes/admin.py) needs a cross-user view to answer the
-- *system-wide* "how many tokens are we burning this week?" question.
-- Without this widening, that endpoint silently returns only the
-- admin's own rows — useless for monitoring the rest of the user base.
--
-- The widening is expressed as an ADDITIONAL policy on each table.
-- Postgres OR's SELECT policies together: a regular user still passes
-- via the owner policy on their own rows, and an admin additionally
-- passes via this policy on ALL rows. Non-admins gain nothing from
-- this policy.
--
-- The admin set is read from the custom postgres setting
-- `app.admin_user_ids` — comma-separated UUIDs. This matches the
-- pattern in 20260522130200 (the aggregator schedule reads
-- `app.environment`). Set it on the production Supabase project:
--   Dashboard → Database → Custom Postgres Config:
--     app.admin_user_ids = '<your-admin-uuid>,<other-admin-uuid>'
-- Local Supabase leaves it unset; the policy matches no one and the
-- admin route remains scoped to its caller's own rows (a sensible
-- sandboxed default for dev).
--
-- The string_to_array + ANY pattern lets us put the allowlist in a
-- single postgres setting rather than reflecting it into Postgres as
-- rows or roles. `coalesce(..., '')` is what makes the unset case
-- safe — `current_setting('app.admin_user_ids', true)` returns NULL
-- when unset, and `string_to_array(NULL, ',')` would in turn return
-- NULL, which `= ANY` evaluates as NULL (treated as false by the
-- USING clause). The coalesce makes the failure mode explicit: unset
-- setting means an empty allowlist.

CREATE POLICY ai_call_log_admin_read ON ai_call_log
    FOR SELECT
    USING (
        auth.uid()::text = ANY (
            string_to_array(
                coalesce(current_setting('app.admin_user_ids', true), ''),
                ','
            )
        )
    );

CREATE POLICY ai_call_log_daily_admin_read ON ai_call_log_daily
    FOR SELECT
    USING (
        auth.uid()::text = ANY (
            string_to_array(
                coalesce(current_setting('app.admin_user_ids', true), ''),
                ','
            )
        )
    );
