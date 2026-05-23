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
-- Design: a small `admins` table is the single source of truth. Earlier
-- drafts used a `current_setting('app.admin_user_ids')` GUC, which
-- required `ALTER DATABASE postgres SET ...` — denied on Supabase Free
-- tier. A table works on every tier and survives Postgres restarts.
--
-- Single source of truth: the Python route (`require_admin` in
-- app/routes/admin.py) also queries this table, so route admittance
-- and RLS widening cannot drift apart. The previous design used a
-- separate `ADMIN_USER_IDS` env var that had to be kept in sync.
--
-- To add an admin (run in Supabase SQL Editor — service_role context):
--   INSERT INTO admins (user_id) VALUES ('<your-uuid>');
-- To remove:
--   DELETE FROM admins WHERE user_id = '<their-uuid>';

CREATE TABLE IF NOT EXISTS admins (
    user_id  UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    added_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE admins FORCE  ROW LEVEL SECURITY;

-- A user can see their own admins row (telling them whether they're
-- admin) but not anyone else's. INSERT / UPDATE / DELETE have no
-- policies → only service_role (Supabase dashboard SQL Editor,
-- migrations) can manage admin membership. Same posture as
-- `ai_call_log`: writes are out-of-band, reads are scoped.
CREATE POLICY admins_self_read ON admins
    FOR SELECT
    USING (user_id = auth.uid());

-- The cross-user SELECT widening on ai_call_log + ai_call_log_daily.
-- Postgres OR's SELECT policies together: a regular user still passes
-- via the owner policy on their own rows; an admin additionally passes
-- via these policies on all rows. The EXISTS subquery executes inside
-- the caller's RLS scope, so it sees only the caller's own admins row
-- (or none) — non-admins get false, admins get true.
CREATE POLICY ai_call_log_admin_read ON ai_call_log
    FOR SELECT
    USING (EXISTS (SELECT 1 FROM admins WHERE user_id = auth.uid()));

CREATE POLICY ai_call_log_daily_admin_read ON ai_call_log_daily
    FOR SELECT
    USING (EXISTS (SELECT 1 FROM admins WHERE user_id = auth.uid()));
