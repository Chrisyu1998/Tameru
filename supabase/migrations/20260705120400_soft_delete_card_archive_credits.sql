-- soft_delete_card() — add the companion-credit archive branch (DESIGN.md §6.7, §8.3)
--
-- When a card is soft-deleted, its statement credits (§8.17) are card
-- consequences with no reassignment target, so they flip to status='archived'
-- — mirroring the AF-subscription → 'cancelled' branch of the existing split
-- cascade (§8.3). All updates run inside the one SECURITY DEFINER function so
-- they commit or none do (no window where the card is gone but its credits are
-- still 'active').
--
-- Body copied from the LATEST definition (migration 20260519120000; the later
-- 20260610120000 changed only grants, not the body — memory.md 2026-07-04) with
-- the card_credits UPDATE added. The subscription split-cascade and the
-- auth.uid()-filtered WHERE clauses are unchanged. Grants re-stated to the
-- post-audit state (REVOKE FROM PUBLIC, anon; GRANT authenticated).

CREATE OR REPLACE FUNCTION soft_delete_card(p_card_id UUID)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE subscriptions
       SET status = CASE
           WHEN name LIKE '% annual fee'
                AND category = 'Memberships'
                AND frequency = 'annual'
               THEN 'cancelled'
           ELSE 'paused'
       END
     WHERE card_id = p_card_id
       AND user_id = auth.uid()
       AND status IN ('active', 'paused');

    -- Companion statement credits (§6.7): archive them. A credit lives on
    -- the card and can't be reassigned, so this mirrors the AF → 'cancelled'
    -- branch above, not the Netflix → 'paused' one.
    UPDATE card_credits
       SET status = 'archived'
     WHERE card_id = p_card_id
       AND user_id = auth.uid()
       AND status = 'active';

    UPDATE cards
       SET status = 'deleted',
           deleted_at = NOW()
     WHERE id = p_card_id
       AND user_id = auth.uid()
       AND status = 'active';
END;
$$;

REVOKE EXECUTE ON FUNCTION soft_delete_card(UUID) FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION soft_delete_card(UUID) TO authenticated;
