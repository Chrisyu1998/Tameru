-- card_credits_confirm() — bulk propose-then-confirm upsert (DESIGN.md §6.7, §8.17)
--
-- POST /card-credits/confirm posts back a checklist of proposed credits (from
-- the lookup, with the user's edits) and this function writes them. It exists
-- for the same reason csv_import_bulk_insert does: the confirm needs an
-- idempotent `ON CONFLICT DO NOTHING`, but the arbiter is the expression +
-- partial index `card_credits_active_name_uniq` on (card_id, lower(name))
-- WHERE status='active', which PostgREST's `on_conflict` param cannot express
-- (42P10 — memory.md 2026-05-17 / 2026-05-19). This function emits the matching
-- predicate directly.
--
-- The name index is the arbiter (not the crid index) because it dedups BOTH
-- idempotency cases: a crid replay (same names) and a re-run of the lookup
-- (new crids, same names) both collide on (card_id, lower(name)). The crid
-- index is defense-in-depth + the chat-rehydrate join key, same split as cards
-- (§8.1). A user who renames a proposed credit before confirming creates a
-- genuinely distinct row (their intent) — correct.
--
-- Period seeding: current_period_start / next_reset_date are seeded here from
-- credit_period_bounds() (single source of truth, shared with the reset sweep)
-- using the user's LOCAL today (users_meta.timezone, else UTC). used_amount
-- starts at 0.
--
-- SECURITY INVOKER: runs as the caller, so auth.uid() resolves to the JWT
-- subject and the card-ownership EXISTS check reads only the caller's own
-- cards under RLS. user_id is hardcoded to auth.uid() (defense in depth beside
-- the WITH CHECK policy), and a forged card_id pointing at another user's card
-- fails the EXISTS filter and is silently dropped. Rows with an invalid
-- cadence are filtered out (rather than aborting the batch on the NOT NULL
-- period columns the CASE would leave null).

CREATE OR REPLACE FUNCTION card_credits_confirm(p_rows jsonb)
RETURNS SETOF card_credits
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_today date;
BEGIN
    -- User-local calendar date; boundaries are computed in the user's tz
    -- (else UTC), the same rule as the reset sweep and §8.17. RLS scopes
    -- the users_meta read to the caller.
    SELECT (now() AT TIME ZONE COALESCE(timezone, 'UTC'))::date
      INTO v_today
      FROM users_meta
     WHERE user_id = auth.uid();
    IF v_today IS NULL THEN
        v_today := (now() AT TIME ZONE 'UTC')::date;
    END IF;

    RETURN QUERY
    INSERT INTO card_credits (
        user_id, card_id, name, amount, cadence, merchant_hint,
        source_urls, verified_at, client_request_id,
        used_amount, current_period_start, next_reset_date, status
    )
    SELECT
        auth.uid(),
        (r->>'card_id')::uuid,
        r->>'name',
        NULLIF(r->>'amount', '')::numeric,
        r->>'cadence',
        NULLIF(lower(r->>'merchant_hint'), ''),
        CASE WHEN jsonb_typeof(r->'source_urls') = 'array'
             THEN ARRAY(SELECT jsonb_array_elements_text(r->'source_urls'))
             ELSE NULL END,
        NULLIF(r->>'verified_at', '')::timestamptz,
        COALESCE(NULLIF(r->>'client_request_id', '')::uuid, gen_random_uuid()),
        0,
        b.period_start,
        b.next_reset,
        'active'
    FROM jsonb_array_elements(p_rows) AS r
    CROSS JOIN LATERAL credit_period_bounds(r->>'cadence', v_today) AS b
    WHERE (r->>'cadence') IN ('monthly', 'quarterly', 'semiannual', 'annual')
      AND EXISTS (
          SELECT 1 FROM cards c
           WHERE c.id = (r->>'card_id')::uuid
             AND c.status = 'active'
      )
    ON CONFLICT (card_id, lower(name)) WHERE status = 'active'
    DO NOTHING
    RETURNING *;
END;
$$;

COMMENT ON FUNCTION card_credits_confirm(jsonb) IS
  'DESIGN.md §6.7 — bulk confirm for POST /card-credits/confirm. Seeds period '
  'bounds via credit_period_bounds(), dedups on the (card_id, lower(name)) '
  'partial index, drops rows whose card_id is not the caller''s active card. '
  'Returns only the rows that landed so the route can reconcile idempotency.';

-- SECURITY INVOKER + RLS fires per row via auth.uid(); no REVOKE (same posture
-- as csv_import_bulk_insert — the default authenticated EXECUTE grant is
-- correct for a user-JWT confirm path).
