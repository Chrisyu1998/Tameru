-- trim_eval_user_ai_call_log() — Day 22 (DESIGN.md §7.10)
--
-- The eval harness runs the production agent loop under a real Supabase
-- user (`eval@tameru.internal`) so RLS fires exactly as it does for a
-- human user (CLAUDE.md invariant 1 — no service-role bypass in eval
-- code). Every eval turn therefore writes `ai_call_log` rows under that
-- user, the same way `app/agent/loop.py` does for any chat turn
-- (invariant 14). CI runs the gate on every eval-relevant PR, so those
-- rows accumulate indefinitely without a sweep.
--
-- This function deletes the eval user's `ai_call_log` rows older than 7
-- days. It does NOT touch any other user's rows — the WHERE clause is
-- pinned to the eval user's id, resolved by email. If the eval user
-- doesn't exist (fresh prod project that never ran an eval), the
-- function is a no-op.
--
-- Why a fixed retention and not the full prune logic: eval rows have no
-- analytic value past the run that produced them — the per-run JSON in
-- evals/runs/ is the durable artifact. Seven days is enough to debug a
-- recent CI failure without letting the table grow unbounded.
--
-- SECURITY DEFINER because pg_cron has no `auth.uid()`. `ai_call_log`
-- has a SELECT-only RLS policy for end users and no UPDATE/DELETE policy
-- at all (DESIGN.md §8.8) — a DELETE must run with definer privileges.
-- `SET search_path = public` closes the DEFINER search-path hijack
-- surface. EXECUTE is revoked from PUBLIC / anon / authenticated and
-- granted only to service_role: an end-user JWT must not be able to
-- call this via PostgREST RPC (see the 2026-05-18 memory.md decision on
-- explicit three-role REVOKE).
--
-- Scheduling lives in supabase/snippets/production_cron.sql, NOT here —
-- migrations apply in dev / test / prod alike and we don't want the job
-- firing in dev / CI (same reasoning as autolog_subscriptions).

CREATE OR REPLACE FUNCTION trim_eval_user_ai_call_log()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    eval_user_id UUID;
BEGIN
    SELECT id
      INTO eval_user_id
      FROM auth.users
     WHERE email = 'eval@tameru.internal'
     LIMIT 1;

    -- No eval user on this project — nothing to trim.
    IF eval_user_id IS NULL THEN
        RETURN;
    END IF;

    -- `ai_call_log` timestamps its rows in the `timestamp` column
    -- (§8.8), not `created_at`.
    DELETE FROM ai_call_log
     WHERE user_id = eval_user_id
       AND timestamp < now() - INTERVAL '7 days';
END;
$$;

-- Least-privilege: name all three role tiers in the REVOKE. REVOKE FROM
-- PUBLIC alone leaves the function reachable under a regular user JWT
-- because 20260515210000_backfill_supabase_grants.sql auto-grants
-- EXECUTE to anon + authenticated on every new public function.
REVOKE EXECUTE ON FUNCTION trim_eval_user_ai_call_log() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION trim_eval_user_ai_call_log() TO service_role;
