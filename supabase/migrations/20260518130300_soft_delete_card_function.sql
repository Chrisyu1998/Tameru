-- soft_delete_card(p_card_id UUID) — Day 19 follow-up (DESIGN.md §8.3)
--
-- Atomic implementation of the card soft-delete + split-cascade. The
-- prior route did three separate PostgREST UPDATEs:
--
--   1. UPDATE subscriptions SET status='cancelled' WHERE … (AF rows)
--   2. UPDATE subscriptions SET status='paused' WHERE … (regular rows)
--   3. UPDATE cards SET status='deleted', deleted_at=NOW() WHERE …
--
-- Each was its own request/transaction, so a failure between passes
-- left the user in an inconsistent visible state: subs partly handled
-- but the card still active, recoverable only on retry. The function
-- body below runs in a single implicit transaction — all three updates
-- commit or none do.
--
-- Recognition of "card annual-fee" subscriptions matches the same
-- triple used by §6.5's GET-side filter and by the Day 19b dual-write
-- name template:
--
--     name LIKE '% annual fee'
--   AND category = 'Subscriptions'
--   AND frequency = 'annual'
--
-- The two-pass UPDATE collapses into one CASE-based UPDATE — same end
-- state, simpler to read inside plpgsql.
--
-- Security model: SECURITY DEFINER so the function can write under the
-- definer's privileges and pg_cron-style scheduled execution would
-- work if ever needed. Every WHERE clause is filtered by
-- `auth.uid()`, which PostgREST populates from the caller's JWT — so
-- a user can only soft-delete their own card. The function is
-- callable by `authenticated` (not service_role only) because
-- end-user JWTs reach it through `client.rpc(...)` in
-- app/routes/cards.py.
--
-- Idempotent: re-calling on an already-deleted card is a no-op
-- (every WHERE filters by `status='active'`).

CREATE OR REPLACE FUNCTION soft_delete_card(p_card_id UUID)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Single-pass split-cascade on companion subscriptions. Pre-RPC
    -- the route did two UPDATEs (cancel AFs, then pause whatever was
    -- still active); CASE inside one statement is the same logic with
    -- no order-of-operations dependency.
    UPDATE subscriptions
       SET status = CASE
           WHEN name LIKE '% annual fee'
                AND category = 'Subscriptions'
                AND frequency = 'annual'
               THEN 'cancelled'
           ELSE 'paused'
       END
     WHERE card_id = p_card_id
       AND user_id = auth.uid()
       AND status IN ('active', 'paused');

    -- Soft-delete the card itself. Same shape as the prior inline
    -- UPDATE in app/routes/cards.py.
    UPDATE cards
       SET status = 'deleted',
           deleted_at = NOW()
     WHERE id = p_card_id
       AND user_id = auth.uid()
       AND status = 'active';
END;
$$;

REVOKE EXECUTE ON FUNCTION soft_delete_card(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soft_delete_card(UUID) TO authenticated;
