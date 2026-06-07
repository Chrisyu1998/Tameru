-- insert_card_with_af — Tier 3 amendment (DESIGN.md §6.6).
--
-- Adds `region`, `base_reward_rate`, and `rewards_currency` to the card
-- INSERT so the confirm route can persist the per-card region (which drives
-- reward-lookup routing) and the JP/TW base-rate reward shape. Signature is
-- unchanged (jsonb, jsonb) so CREATE OR REPLACE is safe and the existing
-- REVOKE/GRANT stay in force. Everything else — the auth.uid() guard, the AF
-- companion-subscription dual-write, the recognition triple — is identical to
-- migration 20260519130000.
--
-- `region` falls back to 'US' if the caller omits it (defense-in-depth; the
-- route always supplies it). `base_reward_rate` / `rewards_currency` are
-- nullable — US cards leave them null and use `multipliers` instead.

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
        client_request_id,
        region,
        base_reward_rate,
        rewards_currency
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
        (p_card ->> 'client_request_id')::uuid,
        COALESCE(p_card ->> 'region', 'US'),
        NULLIF(p_card ->> 'base_reward_rate', '')::numeric,
        p_card ->> 'rewards_currency'
    )
    RETURNING * INTO new_card;

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
