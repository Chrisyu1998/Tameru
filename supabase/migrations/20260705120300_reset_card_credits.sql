-- reset_card_credits() — daily calendar-boundary reset sweep (DESIGN.md §6.7, §8.17)
--
-- Same family as autolog_subscriptions() (§6.5, migration 20260518130200): a
-- forward-only, idempotent, advisory-locked pg_cron sweep that runs as the
-- service role (no user JWT — this is a new sanctioned service-role caller,
-- CLAUDE.md invariant 1; enumerate it there and in DESIGN.md §9.1). For each
-- active credit whose next_reset_date has arrived in the user's local calendar,
-- it zeroes used_amount and advances the period to the next boundary via
-- credit_period_bounds() (the same helper the confirm seeds with — no drift).
--
-- Missable-recoverable: a skipped night leaves a credit showing last period's
-- usage for ≤24h. Naturally idempotent — after the advance, next_reset_date is
-- in the future, so a second run the same day is a no-op. Forward-only — it
-- never backfills a missed period; it jumps straight to the current one.
--
-- NO two-sided advisory lock with the used_amount write path (PATCH
-- /card-credits/{id}). The sweep zeroes used_amount only at a period boundary,
-- so a manual set-absolute edit colliding with it is period-boundary-ambiguous,
-- visible, and self-correcting (the user re-enters if zeroed). This is
-- deliberately NOT the memory-prune two-sided lock (memory.md 2026-05-18) —
-- that guarded silent, permanent loss; this loss is neither silent nor
-- permanent. The advisory lock here guards only against concurrent CRON runs.
-- (The Phase-2 ledger-bridge increment closes its own read-modify-write window
-- with an atomic single-statement LEAST(...) UPDATE + a current_period_start
-- guard, not a lock.)
--
-- Boundaries are computed in the user's timezone (users_meta.timezone) when
-- set, else UTC — matching the confirm seed and the digest send window.
-- SECURITY DEFINER because pg_cron has no auth.uid(); SET search_path = public
-- closes the DEFINER-hijack surface.

CREATE OR REPLACE FUNCTION reset_card_credits()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    cr RECORD;
    v_ps date;
    v_nr date;
BEGIN
    -- Concurrent-run guard. Transaction-scoped (auto-released on COMMIT /
    -- ROLLBACK so an error can't strand the lock on a pooled connection).
    -- Slot 8830732 — reserved for this function; slot 8830731 is
    -- autolog_subscriptions (DESIGN.md §14.3). Document any future cron
    -- lock slots alongside these.
    IF NOT pg_try_advisory_xact_lock(8830732) THEN
        RETURN;
    END IF;

    FOR cr IN
        SELECT c.id, c.cadence, cm.timezone
          FROM card_credits c
          LEFT JOIN users_meta cm ON cm.user_id = c.user_id
         WHERE c.status = 'active'
           AND c.next_reset_date <= (now() AT TIME ZONE COALESCE(cm.timezone, 'UTC'))::date
    LOOP
        -- Phase 2 (§8.18): snapshot the closing period into
        -- card_credit_history here before zeroing. Phase 1 skips it.
        SELECT b.period_start, b.next_reset
          INTO v_ps, v_nr
          FROM credit_period_bounds(
                   cr.cadence,
                   (now() AT TIME ZONE COALESCE(cr.timezone, 'UTC'))::date
               ) AS b;

        UPDATE card_credits
           SET used_amount = 0,
               current_period_start = v_ps,
               next_reset_date = v_nr
         WHERE id = cr.id
           AND status = 'active';
    END LOOP;

    -- No explicit unlock — pg_try_advisory_xact_lock auto-releases on
    -- transaction commit / rollback.
END;
$$;

-- Cron scheduling lives in supabase/snippets/production_cron.sql, NOT here, so
-- the sweep doesn't fire in dev / CI where tests call reset_card_credits()
-- directly under a per-test seed (same posture as autolog_subscriptions).
--
-- Service-role only: the REVOKE list names anon + authenticated explicitly
-- because the default-privilege backfill (20260515210000) grants them EXECUTE
-- on every new public function, and REVOKE FROM PUBLIC alone does not dislodge
-- role grants (memory.md 2026-05-18).
REVOKE EXECUTE ON FUNCTION reset_card_credits() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION reset_card_credits() TO service_role;
