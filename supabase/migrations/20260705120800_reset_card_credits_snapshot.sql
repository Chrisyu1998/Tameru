-- reset_card_credits() — add the Phase-2 period snapshot (DESIGN.md §6.7, §8.18)
--
-- Phase 1 (migration 20260705120300) shipped the reset sweep with a documented
-- gap: "Phase 2 (§8.18): snapshot the closing period into card_credit_history
-- here before zeroing. Phase 1 skips it." Now that card_credit_history exists
-- (migration 20260705120700), this CREATE OR REPLACE fills the gap.
--
-- The only change from the Phase-1 body is the INSERT ... SELECT into
-- card_credit_history immediately before the zero-and-advance UPDATE. It reads
-- the credit's CLOSING state (current_period_start / next_reset_date /
-- used_amount / amount / name) straight off the row, so the snapshot is exactly
-- what the period was before it rolled. ON CONFLICT DO NOTHING on the
-- (card_credit_id, period_start) unique index keeps it idempotent — a same-day
-- rerun (already a no-op on card_credits because next_reset_date has advanced)
-- also no-ops the snapshot.
--
-- Everything else is verbatim from 120300: forward-only, advisory-locked
-- against concurrent cron runs (slot 8830732), boundaries in the user's tz else
-- UTC, SECURITY DEFINER + SET search_path. See 120300 for the full rationale.

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
        -- Phase 2 (§8.18): snapshot the closing period before zeroing. Reads
        -- the credit's current state directly; DO NOTHING keeps it idempotent
        -- against a same-day re-run.
        INSERT INTO card_credit_history (
            user_id, card_credit_id, name, amount, used_amount,
            period_start, period_end
        )
        SELECT user_id, id, name, amount, used_amount,
               current_period_start, next_reset_date
          FROM card_credits
         WHERE id = cr.id
        ON CONFLICT (card_credit_id, period_start) DO NOTHING;

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
END;
$$;

REVOKE EXECUTE ON FUNCTION reset_card_credits() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION reset_card_credits() TO service_role;
