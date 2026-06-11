-- autolog_subscriptions(): anchor the month/quarter/year advance to the
-- subscription's original day-of-month (2026-06 audit P2-8).
--
-- The previous advance was `sub.next_billing_date + INTERVAL '1 month'` —
-- from the previously *computed* date, not the subscription's anchor day.
-- Postgres clamps Jan 31 + 1 month to Feb 28, and every later advance
-- starts from the clamped value, so the day-of-month anchor was lost
-- permanently (Feb 28 → Mar 28 → Apr 28 …). Quarterly/annual had the same
-- shape (Feb-29 annuals lose the 29th after a leap year). Since frequency
-- and start_date are immutable post-create (memory.md 2026-05-19), the
-- user's only repair was cancel-and-re-add — which re-drifts at the next
-- short month.
--
-- Fix: derive the anchor day from `start_date` (immutable, so the anchor
-- can never move) and clamp per-month instead of compounding:
--   advance year/month via the interval, then restore the day to
--   LEAST(anchor_day, days_in_target_month).
-- Jan 31 → Feb 28 → Mar 31 → Apr 30 → May 31 … Weekly advances stay plain
-- +7 days (day-of-week anchoring is preserved by construction).
--
-- Everything else — advisory lock, idempotent ON CONFLICT insert,
-- RETURNING-gated advance, forward-only semantics — is unchanged from
-- 20260518130200.

CREATE OR REPLACE FUNCTION autolog_subscriptions()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    sub RECORD;
    inserted_id UUID;
    v_next DATE;
    v_anchor_day INT;
BEGIN
    -- Concurrent-run guard. Transaction-scoped — released automatically
    -- on COMMIT or ROLLBACK so an unexpected error inside the function
    -- can't leave the lock held on a pooled connection (which would
    -- silently jam every subsequent cron run). A second invocation that
    -- arrives mid-run sees the lock already held and exits silently.
    IF NOT pg_try_advisory_xact_lock(8830731) THEN
        RETURN;
    END IF;

    FOR sub IN
        SELECT id, user_id, card_id, name, amount, frequency,
               next_billing_date, start_date, category
          FROM subscriptions
         WHERE status = 'active'
           AND next_billing_date <= CURRENT_DATE
    LOOP
        -- Idempotent insert. ON CONFLICT predicate must match the
        -- partial-index predicate exactly.
        INSERT INTO transactions
            (user_id, card_id, subscription_id, merchant, amount, date,
             category, source)
        VALUES
            (sub.user_id, sub.card_id, sub.id, sub.name, sub.amount,
             sub.next_billing_date, sub.category, 'auto_logged')
        ON CONFLICT (subscription_id, date)
            WHERE status = 'active' AND subscription_id IS NOT NULL
            DO NOTHING
        RETURNING id INTO inserted_id;

        -- Advance next_billing_date only when the insert actually fired.
        -- ON CONFLICT DO NOTHING leaves `inserted_id` NULL on the conflict
        -- branch, so the cron stays idempotent if it runs twice on the
        -- same day.
        IF inserted_id IS NOT NULL THEN
            IF sub.frequency = 'weekly' THEN
                v_next := sub.next_billing_date + INTERVAL '7 days';
            ELSE
                -- Year/month from the plain interval advance (Postgres
                -- clamps the day when the target month is short)…
                v_next := (sub.next_billing_date + CASE sub.frequency
                               WHEN 'monthly'   THEN INTERVAL '1 month'
                               WHEN 'quarterly' THEN INTERVAL '3 months'
                               WHEN 'annual'    THEN INTERVAL '1 year'
                           END)::date;
                -- …then restore the day to the start_date anchor, clamped
                -- to the target month's length, so a short month never
                -- permanently shifts later billing dates.
                v_anchor_day := EXTRACT(DAY FROM sub.start_date)::int;
                v_next := make_date(
                    EXTRACT(YEAR FROM v_next)::int,
                    EXTRACT(MONTH FROM v_next)::int,
                    LEAST(
                        v_anchor_day,
                        EXTRACT(DAY FROM (
                            date_trunc('month', v_next)
                            + INTERVAL '1 month - 1 day'
                        ))::int
                    )
                );
            END IF;

            UPDATE subscriptions
               SET next_billing_date = v_next
             WHERE id = sub.id;
        END IF;
    END LOOP;

    -- No explicit unlock — pg_try_advisory_xact_lock auto-releases on
    -- transaction commit / rollback.
END;
$$;

-- CREATE OR REPLACE re-attaches the default-privilege grants from
-- 20260515210000 (anon, authenticated, service_role), so the full
-- three-role REVOKE must be re-applied here (memory.md 2026-05-18).
REVOKE EXECUTE ON FUNCTION autolog_subscriptions() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION autolog_subscriptions() TO service_role;
