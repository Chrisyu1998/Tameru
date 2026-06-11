-- autolog_subscriptions() — Day 19 (DESIGN.md §6.5, §14.3)
--
-- Runs daily via pg_cron in production (scheduled separately in
-- supabase/snippets/production_cron.sql so dev doesn't auto-run it). Inserts
-- a transaction for every active subscription whose next_billing_date has
-- come due, then advances next_billing_date by one period.
--
-- Idempotency: the partial unique index `transactions_subscription_date_unique`
-- on (subscription_id, date) WHERE status = 'active' AND subscription_id IS
-- NOT NULL makes a duplicate insert into the same (subscription, date) slot
-- a no-op. The `ON CONFLICT` clause MUST repeat the partial-index predicate
-- exactly or Postgres refuses to match it (no unique or exclusion constraint
-- matching the ON CONFLICT specification).
--
-- Concurrency: the advisory lock (slot 8830731 — reserved for this function;
-- document any future cron-function lock slots alongside this one in
-- DESIGN.md §14.3) makes parallel invocations a no-op for the second caller.
-- Without it, two concurrent runs could race: each reads the same active
-- subscriptions, both insert, the second one's INSERT collides on the unique
-- index, but the second one also tries to advance next_billing_date — and
-- without the lock-out we could advance twice.
--
-- We use `pg_try_advisory_xact_lock` (transaction-scoped) rather than
-- `pg_try_advisory_lock` (session-scoped) so the lock is released
-- automatically on COMMIT or ROLLBACK. Postgres + Supabase use pooled
-- connections, so a session-scoped lock held when an unexpected error
-- rolls the function back can survive past the transaction — leaving the
-- lock owned by a reusable connection and silently jamming every
-- subsequent cron run with a no-op. Same shape as the Day 17
-- `prune_user_memory` lock (`pg_try_advisory_xact_lock`).
--
-- Forward-only auto-log: per §8.3, propose_subscription clamps
-- next_billing_date to `today + 1 period` at confirm time when start_date is
-- in the past. The function itself does NOT backfill — it advances by one
-- period per cron run. If next_billing_date is somehow multiple periods
-- behind (shouldn't happen given the clamp, but defense-in-depth), the
-- subscription catches up one day at a time, which is friendlier than
-- spamming the dashboard with N rows on a single cron run.
--
-- Cardless subscriptions: card_id may be NULL since the
-- subscriptions_card_id_nullable migration. The INSERT passes it through
-- to transactions.card_id (already nullable since Day 2).

CREATE OR REPLACE FUNCTION autolog_subscriptions()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    sub RECORD;
    inserted_id UUID;
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
               next_billing_date, category
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
            UPDATE subscriptions
               SET next_billing_date = CASE sub.frequency
                       WHEN 'weekly'    THEN sub.next_billing_date + INTERVAL '1 week'
                       WHEN 'monthly'   THEN sub.next_billing_date + INTERVAL '1 month'
                       WHEN 'quarterly' THEN sub.next_billing_date + INTERVAL '3 months'
                       WHEN 'annual'    THEN sub.next_billing_date + INTERVAL '1 year'
                   END
             WHERE id = sub.id;
        END IF;
    END LOOP;

    -- No explicit unlock — pg_try_advisory_xact_lock auto-releases on
    -- transaction commit / rollback.
END;
$$;

-- Cron scheduling lives in supabase/snippets/production_cron.sql, NOT in
-- this migration. Migrations apply in every environment (dev, test,
-- prod) and we deliberately don't want the cron firing in dev / test —
-- see DESIGN.md §14.3.
--
-- Privilege model: the function is SECURITY DEFINER so it runs with the
-- definer's privileges (postgres in local dev / Supabase prod) and can
-- read/write `subscriptions` and `transactions` regardless of the
-- caller's RLS scope — exactly what pg_cron and the contract test
-- suite need. We explicitly REVOKE EXECUTE from PUBLIC and grant only
-- to service_role and postgres so an authenticated end-user JWT cannot
-- call it via PostgREST RPC. The Day 17 prune_user_memory function
-- uses the same posture (migration 20260518120000).
REVOKE EXECUTE ON FUNCTION autolog_subscriptions() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autolog_subscriptions() TO service_role;
