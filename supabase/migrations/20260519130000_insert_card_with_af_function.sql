-- insert_card_with_af(p_card jsonb, p_af jsonb) — Day 19b (DESIGN.md §6.5, §8.1)
--
-- Atomic implementation of the card-confirm + companion AF-subscription
-- dual-write. Replaces the prior best-effort sequence (cards INSERT
-- followed by a try/except subscriptions INSERT) which could leave a
-- card created without its AF tracker on any failure of the second
-- write. Same pattern as Day 19's soft_delete_card RPC
-- (20260518130300) — one SQL transaction, both inserts commit or
-- neither does.
--
-- The Python route can't do this from two PostgREST calls because the
-- Supabase client has no multi-statement transaction primitive.
--
-- p_card: jsonb payload mirroring the validated CardConfirmRequest.
--         Required keys (NOT NULL columns): user_id, name, issuer,
--         network, client_request_id. Optional: program, multipliers,
--         last_four, annual_fee, color, source_urls. The route already
--         enforces auth-context user_id; the function additionally
--         filters cards by `user_id = auth.uid()` on the inserted row.
--
-- p_af: jsonb payload OR null. When present, must contain
--       `next_annual_fee_date` (date as ISO string). The function reads
--       `name` and `annual_fee` from p_card to populate the companion
--       subscription row. Caller passes p_af = null when the user
--       didn't supply a renewal date or when annual_fee is zero/null
--       — the route is responsible for that gate.
--
-- Errors propagate the same way they would from a direct PostgREST
-- INSERT: unique-violation on `cards_active_identity_uniq` (natural-key
-- collision, surfaces as 409) or `cards_active_client_request_id_unique`
-- (crid replay — the route's same-crid short-circuit catches this
-- before calling). Either rolls back both inserts.
--
-- AF subscription shape — must match the Day 19 soft_delete_card
-- recognition triple so the split-cascade flips it to 'cancelled' on
-- card soft-delete:
--   - name LIKE '% annual fee'
--   - category = 'Memberships'
--   - frequency = 'annual'
-- The literal 'Memberships' is the post-§6.5-rename canonical
-- (migration 20260519120000). If this drifts, the cascade silently
-- misses AFs.
--
-- Security: SECURITY DEFINER + every WHERE filtered by `auth.uid()`.
-- A user cannot insert a card under another user's id even with a
-- spoofed `user_id` in p_card, because the RETURNING SELECT filters
-- by `auth.uid()` and would return zero rows (function raises).
-- `client_request_id` on the AF subscription is server-minted via
-- gen_random_uuid() — the user never sees a separate parse card for
-- it, so there's no client-supplied crid in scope.

CREATE OR REPLACE FUNCTION insert_card_with_af(
    p_card jsonb,
    p_af jsonb
)
RETURNS cards
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    new_card cards%ROWTYPE;
    af_date date;
BEGIN
    -- Defense-in-depth: reject a payload whose user_id doesn't match
    -- the JWT caller. The route already builds p_card from the
    -- authenticated user context, but a future caller wiring this RPC
    -- from a different surface shouldn't be able to bypass that.
    IF (p_card ->> 'user_id')::uuid IS DISTINCT FROM auth.uid() THEN
        RAISE EXCEPTION 'user_id in p_card does not match auth.uid()';
    END IF;

    INSERT INTO cards (
        user_id,
        name,
        issuer,
        network,
        program,
        multipliers,
        last_four,
        annual_fee,
        color,
        source_urls,
        client_request_id
    )
    VALUES (
        (p_card ->> 'user_id')::uuid,
        p_card ->> 'name',
        p_card ->> 'issuer',
        p_card ->> 'network',
        COALESCE(p_card ->> 'program', 'Other'),
        COALESCE(p_card -> 'multipliers', '{}'::jsonb),
        p_card ->> 'last_four',
        NULLIF(p_card ->> 'annual_fee', '')::numeric,
        p_card ->> 'color',
        COALESCE(
            ARRAY(SELECT jsonb_array_elements_text(p_card -> 'source_urls')),
            '{}'::text[]
        ),
        (p_card ->> 'client_request_id')::uuid
    )
    RETURNING * INTO new_card;

    -- AF companion subscription. Only when p_af is non-null AND the
    -- card has a non-zero annual_fee — the route is supposed to gate
    -- this, but a redundant DB-side guard prevents an annual_fee=0
    -- card from accidentally getting a $0/year auto-log.
    IF p_af IS NOT NULL AND new_card.annual_fee IS NOT NULL AND new_card.annual_fee > 0 THEN
        af_date := (p_af ->> 'next_annual_fee_date')::date;
        IF af_date IS NULL THEN
            RAISE EXCEPTION 'p_af is non-null but next_annual_fee_date is missing';
        END IF;

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
            new_card.user_id,
            new_card.id,
            new_card.name || ' annual fee',
            new_card.annual_fee,
            'annual',
            af_date,
            af_date,
            'Memberships',
            'active',
            gen_random_uuid()
        );
    END IF;

    RETURN new_card;
END;
$$;

REVOKE EXECUTE ON FUNCTION insert_card_with_af(jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insert_card_with_af(jsonb, jsonb) TO authenticated;
