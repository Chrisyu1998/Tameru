-- update_card_af(p_card_id, p_annual_fee, p_set_annual_fee, p_next_annual_fee_date, p_set_next_date)
-- — Day 19b (DESIGN.md §6.5, §8.1).
--
-- Atomic cascade for AF-touching card patches. `PATCH /cards/{id}`
-- routes through this function when the patch body contains either
-- `annual_fee` (existing field) or the new `next_annual_fee_date`
-- virtual field. Updates `cards.annual_fee` and the companion AF
-- subscription's `amount` / `next_billing_date` in one SQL transaction
-- so the cron auto-log always charges the amount the user just typed.
--
-- The two `p_set_*` booleans distinguish "field omitted from patch"
-- from "field explicitly set to null." (`p_annual_fee = null` with
-- `p_set_annual_fee = true` clears the column; with
-- `p_set_annual_fee = false`, the value is ignored.) jsonb would have
-- worked too but the payload is two fields, so two pairs of args read
-- more clearly at the route call site.
--
-- Branches:
--   1. If p_set_annual_fee: update cards.annual_fee.
--   2. If an active AF subscription exists for this card (recognition
--      triple: name LIKE '% annual fee' AND category='Memberships'
--      AND frequency='annual' AND status='active'):
--        - p_set_annual_fee with a positive amount → mirror onto
--          subscriptions.amount.
--        - p_set_annual_fee with null or <=0 → cancel the AF sub.
--          subscriptions.amount is NOT NULL so we can't store null;
--          and a $0 AF means there's nothing to auto-log anyway.
--          cards.annual_fee still reflects the patch (null or 0).
--        - p_set_next_date AND p_next_annual_fee_date IS NOT NULL →
--          update subscriptions.next_billing_date. (start_date stays
--          immutable per §8.3.)
--        - p_set_next_date AND p_next_annual_fee_date IS NULL → flip
--          subscription status to 'cancelled' (stop tracking).
--          cards.annual_fee keeps the at-cancel snapshot.
--   3. Else (no active AF subscription): if p_set_next_date AND
--      p_next_annual_fee_date IS NOT NULL AND the resulting
--      cards.annual_fee > 0, insert a fresh AF subscription. This is
--      the re-enable path — a card whose AF tracking was previously
--      cancelled gets re-tracked.
--
-- 404 case: if cards.id doesn't match a row owned by auth.uid(), the
-- UPDATE matches zero rows and the function returns nothing. The
-- route handles this by checking `resp.data` and raising 404, matching
-- the pre-RPC behavior.
--
-- Security: SECURITY DEFINER + every WHERE filtered by auth.uid().

CREATE OR REPLACE FUNCTION update_card_af(
    p_card_id uuid,
    p_annual_fee numeric,
    p_set_annual_fee boolean,
    p_next_annual_fee_date date,
    p_set_next_date boolean
)
RETURNS cards
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    updated_card cards%ROWTYPE;
    existing_af subscriptions%ROWTYPE;
    effective_annual_fee numeric;
BEGIN
    -- 1. Update cards.annual_fee if requested.
    IF p_set_annual_fee THEN
        UPDATE cards
           SET annual_fee = p_annual_fee
         WHERE id = p_card_id
           AND user_id = auth.uid()
           AND status = 'active'
         RETURNING * INTO updated_card;
    ELSE
        SELECT * INTO updated_card
          FROM cards
         WHERE id = p_card_id
           AND user_id = auth.uid()
           AND status = 'active';
    END IF;

    -- If the card doesn't exist (or isn't ours / is deleted), return
    -- an empty row. The route checks for this and raises 404.
    IF updated_card.id IS NULL THEN
        RETURN NULL;
    END IF;

    effective_annual_fee := updated_card.annual_fee;

    -- 2. Look for an active companion AF subscription.
    SELECT * INTO existing_af
      FROM subscriptions
     WHERE card_id = p_card_id
       AND user_id = auth.uid()
       AND name LIKE '% annual fee'
       AND category = 'Memberships'
       AND frequency = 'annual'
       AND status = 'active'
     LIMIT 1;

    IF existing_af.id IS NOT NULL THEN
        -- AF sub exists — cascade edits onto it.
        IF p_set_annual_fee THEN
            IF p_annual_fee IS NULL OR p_annual_fee <= 0 THEN
                -- The user effectively turned the AF off by clearing
                -- or zeroing the amount. subscriptions.amount is NOT
                -- NULL so we can't mirror a null value, and storing 0
                -- would auto-log $0 every year. Cancel the tracker
                -- instead — same outcome as an explicit
                -- next_annual_fee_date=null patch. cards.annual_fee
                -- still receives the patch (handled in step 1 above).
                UPDATE subscriptions
                   SET status = 'cancelled'
                 WHERE id = existing_af.id
                   AND user_id = auth.uid();
                -- Skip any next_date cascade below — the sub is
                -- already cancelled and a later branch could try to
                -- mutate a row we just flipped.
                RETURN updated_card;
            ELSE
                UPDATE subscriptions
                   SET amount = p_annual_fee
                 WHERE id = existing_af.id
                   AND user_id = auth.uid();
            END IF;
        END IF;

        IF p_set_next_date THEN
            IF p_next_annual_fee_date IS NULL THEN
                -- Stop tracking — cancel the AF subscription.
                UPDATE subscriptions
                   SET status = 'cancelled'
                 WHERE id = existing_af.id
                   AND user_id = auth.uid();
            ELSE
                UPDATE subscriptions
                   SET next_billing_date = p_next_annual_fee_date
                 WHERE id = existing_af.id
                   AND user_id = auth.uid();
            END IF;
        END IF;

    ELSE
        -- 3. Re-enable path: no active AF sub, but the user supplied
        -- a renewal date and the (post-update) annual_fee is > 0.
        IF p_set_next_date
           AND p_next_annual_fee_date IS NOT NULL
           AND effective_annual_fee IS NOT NULL
           AND effective_annual_fee > 0 THEN
            INSERT INTO subscriptions (
                user_id,
                card_id,
                name,
                amount,
                frequency,
                start_date,
                next_billing_date,
                category,
                status,
                client_request_id
            )
            VALUES (
                updated_card.user_id,
                updated_card.id,
                updated_card.name || ' annual fee',
                effective_annual_fee,
                'annual',
                p_next_annual_fee_date,
                p_next_annual_fee_date,
                'Memberships',
                'active',
                gen_random_uuid()
            );
        END IF;
    END IF;

    RETURN updated_card;
END;
$$;

REVOKE EXECUTE ON FUNCTION update_card_af(uuid, numeric, boolean, date, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION update_card_af(uuid, numeric, boolean, date, boolean) TO authenticated;
