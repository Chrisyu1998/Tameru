-- prune_user_memory + nightly schedule + upsert coordination — Day 17 (DESIGN.md §7.6)
--
-- This migration ships two coupled changes:
--
--   1. `prune_user_memory()` — the nightly sweep itself.
--   2. `CREATE OR REPLACE` of `upsert_user_memory_fact` (defined in
--      20260517130100) so that distillation writes acquire the *same*
--      per-user advisory lock the sweep checks. Without this, the
--      sweep's `pg_try_advisory_xact_lock` would never conflict with
--      anything — distillation's upsert doesn't take the lock by
--      default — and a freshly reinforced row could still be ranked
--      "to delete" inside the trim CTE and removed in the same xact.
--      Splitting the two sides across separate migrations would let
--      one land without the other and silently re-introduce the race.
--
-- prune_user_memory does a two-step sweep:
--
--   1. time decay — DELETE rows older than 90 days without reinforcement.
--   2. capacity trim — for each user with > 60 remaining rows, keep the
--      top 60 by `relevance_score / (1 + days_since_reinforced / 30)` and
--      delete the rest. Pure-SQL recency × relevance: day 0 = full score,
--      day 30 = ½, day 60 = ⅓, day 90 = ¼ (then step 1 deletes it anyway).
--      Ties broken by `reinforced_at DESC` so older equal-scored rows fall
--      past the rank-60 cutoff first. `relevance_score` defaults to 0.5
--      so ties will be common.
--
-- SECURITY DEFINER because pg_cron has no `auth.uid()` — under
-- SECURITY INVOKER the RLS predicate `user_id = auth.uid()` compares to
-- NULL and zero rows match. Joins the sanctioned-RLS-bypass list with the
-- subscription auto-logger and the ai_call_log rollup (CLAUDE.md
-- invariants 1 and 14). `SET search_path = public` closes the classic
-- DEFINER hijacking surface (an attacker who can CREATE FUNCTION in
-- another schema could otherwise shadow now()/etc. and have their code
-- execute as the owner).
--
-- Two-sided advisory lock:
--   * Cron: `pg_try_advisory_xact_lock(hashtextextended(user_id::text, 0))`
--     for each over-cap user. Non-blocking; on `false` the user is skipped
--     this run and picked up tomorrow.
--   * Upsert (this file's CREATE OR REPLACE): `pg_advisory_xact_lock(...)`
--     with the SAME key. Blocking. A held lock means cron's trim is in
--     progress for this user — the wait is sub-second because the trim
--     is a single CTE DELETE, well inside distillation's BackgroundTask
--     budget. Sessions are distinct (cron is a postgres-role daemon
--     session; distillation is an authenticated PostgREST session), so
--     advisory locks across them actually conflict.
--
-- Distillation is allowed to push a user over 60 between sweeps. The
-- chat renderer's LIMIT 60 hides the overflow; the Settings memory page
-- shows it briefly. Tolerated 24h-bounded inconsistency, by design.

-- Writer side of the coordination. Replaces the Day 16 definition in
-- 20260517130100_user_memory_dedup_index.sql so distillation upserts
-- block briefly while cron is trimming the same user. CREATE OR REPLACE
-- preserves the existing `GRANT EXECUTE ... TO authenticated` from the
-- Day 16 migration — distillation calls this RPC under the user's JWT.
CREATE OR REPLACE FUNCTION upsert_user_memory_fact(
    p_fact            text,
    p_category        text,
    p_relevance_score numeric
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_id uuid;
BEGIN
    -- Serialize against prune_user_memory()'s capacity-trim step for
    -- this user. Same key shape so the sweep's pg_try_advisory_xact_lock
    -- and our blocking acquire conflict. Without this acquire, the
    -- sweep's lock has nothing to conflict with and a row reinforced
    -- mid-trim could still be deleted (Codex review caught this).
    PERFORM pg_advisory_xact_lock(hashtextextended(auth.uid()::text, 0));

    INSERT INTO user_memory (user_id, fact, category, relevance_score)
    VALUES (auth.uid(), p_fact, p_category, p_relevance_score)
    ON CONFLICT (user_id, category, lower(fact))
    DO UPDATE SET
        reinforced_at   = now(),
        relevance_score = GREATEST(user_memory.relevance_score, EXCLUDED.relevance_score)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;


CREATE OR REPLACE FUNCTION prune_user_memory()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_user_id uuid;
BEGIN
    -- Step 1: time decay. Cross-user single DELETE — no advisory lock
    -- needed because a stale row that distillation is concurrently
    -- reinforcing would either (a) already have reinforced_at advanced
    -- to now() before our snapshot (then it doesn't match the predicate
    -- and survives) or (b) be in the middle of an UPDATE that holds a
    -- row lock and will serialize with our DELETE. Either outcome is
    -- correct.
    DELETE FROM user_memory
     WHERE reinforced_at < now() - interval '90 days';

    -- Step 2: capacity trim. One user at a time, each guarded by a
    -- per-user advisory xact lock so distillation can't interleave.
    -- The outer SELECT lists candidate users *after* step 1's deletes.
    FOR v_user_id IN
        SELECT user_id
          FROM user_memory
         GROUP BY user_id
        HAVING COUNT(*) > 60
    LOOP
        IF NOT pg_try_advisory_xact_lock(
            hashtextextended(v_user_id::text, 0)
        ) THEN
            -- Distillation is writing this user's rows right now. Skip;
            -- the next nightly run picks them up. Over-cap for 24h is
            -- invisible to the agent because render_user_memory caps at
            -- 60 (DESIGN.md §7.6).
            CONTINUE;
        END IF;

        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       ORDER BY
                           (relevance_score
                              / (1.0
                                  + GREATEST(
                                        0,
                                        (now()::date - reinforced_at::date)
                                    ) / 30.0)
                           ) DESC,
                           reinforced_at DESC
                   ) AS rnk
              FROM user_memory
             WHERE user_id = v_user_id
        )
        DELETE FROM user_memory um
         USING ranked r
         WHERE um.id = r.id
           AND r.rnk > 60;
    END LOOP;
END;
$$;

-- Allow nothing through PostgREST. This function is only meaningfully
-- callable by superuser (pg_cron) or service_role (manual ops / tests).
-- A logged-in user has no business invoking the cross-user sweep, and
-- the SECURITY DEFINER + lack of args makes accidental exposure
-- expensive.
--
-- The `REVOKE FROM` list must include `anon, authenticated` explicitly:
-- 20260515210000_backfill_supabase_grants.sql sets
-- `ALTER DEFAULT PRIVILEGES ... GRANT ALL ON FUNCTIONS TO anon,
-- authenticated, service_role`, so a newly-created function in the
-- public schema receives execute grants on those roles automatically.
-- `REVOKE FROM PUBLIC` is a different grant tier and does not dislodge
-- role-specific grants — without naming the roles here, any logged-in
-- user could call this DEFINER function through PostgREST (Codex review
-- caught this).
REVOKE EXECUTE ON FUNCTION prune_user_memory() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION prune_user_memory() TO service_role;


-- Nightly schedule. 03:00 UTC matches the off-peak slot for the
-- expected v1 user base. Same prod-and-dev posture as the (forthcoming)
-- subscription auto-logger — no env gating; running the sweep against a
-- nearly-empty dev DB deletes nothing.
SELECT cron.schedule(
    'prune-memory',
    '0 3 * * *',
    'SELECT prune_user_memory();'
);
