-- card_credit_apply_usage() — Phase-2 ledger-bridge increment (DESIGN.md §6.7)
--
-- The commit path for the "count $X toward {credit}?" suggestion: when the user
-- taps it, POST /card-credits/{id}/apply calls this. It increments used_amount
-- by the matched transaction's amount in a SINGLE atomic statement — no
-- read-modify-write window — so two concurrent bridge taps (different
-- transactions onto the same credit) both land without a lost update, and a tap
-- racing the daily reset can't clobber the fresh period.
--
-- Design choices that make it forge-proof and drift-proof:
--   * It JOINS the transaction rather than trusting a client-sent amount, so a
--     caller cannot inflate a credit by posting a fake delta. RLS (SECURITY
--     INVOKER → runs as the authenticated caller) scopes BOTH card_credits and
--     transactions to auth.uid(), so a foreign credit_id or transaction_id
--     simply yields no matching row → empty return (the route maps that to a
--     409). Card-ownership is additionally enforced by the card_credits UPDATE
--     policy's WITH CHECK.
--   * Period guard `t.date >= cc.current_period_start` (lower bound only): a
--     spend dated before the current period (an old receipt, or a spend from
--     the period that just reset) does not count. A future-dated spend counts
--     toward the current period — a benign <24h pre-sweep edge; deliberately NO
--     upper bound, which would make the tap a silent no-op (TODO.md).
--   * `t.card_id = cc.card_id`: a credit lives on a card; only that card's
--     spend counts. This also excludes cardless (card_id NULL) transactions.
--   * Clamp `GREATEST(0, LEAST(amount, used_amount + delta))`: over-cap clamps
--     at the allowance; a refund (negative amount) floors at 0. When amount is
--     NULL (allowance not yet set) there is no upper clamp — just floor at 0.
--
-- SECURITY INVOKER + the default authenticated EXECUTE grant (same posture as
-- card_credits_confirm / credit_period_bounds — no REVOKE): RLS does the
-- isolation, so the function leaks nothing a direct table write wouldn't.

CREATE OR REPLACE FUNCTION card_credit_apply_usage(
    p_credit_id uuid,
    p_transaction_id uuid
)
RETURNS SETOF card_credits
LANGUAGE sql
SECURITY INVOKER
SET search_path = public
AS $$
    UPDATE card_credits cc
       SET used_amount = GREATEST(
               0,
               CASE WHEN cc.amount IS NULL
                    THEN cc.used_amount + t.amount
                    ELSE LEAST(cc.amount, cc.used_amount + t.amount)
               END
           )
      FROM transactions t
     WHERE cc.id = p_credit_id
       AND cc.status = 'active'
       AND t.id = p_transaction_id
       AND t.status = 'active'
       AND t.card_id = cc.card_id
       AND t.date >= cc.current_period_start
    RETURNING cc.*;
$$;

COMMENT ON FUNCTION card_credit_apply_usage(uuid, uuid) IS
  'DESIGN.md §6.7 Phase 2 — atomic ledger-bridge increment for '
  'POST /card-credits/{id}/apply. Adds the joined transaction''s amount to '
  'used_amount (clamped [0, allowance]), guarded on same-card + '
  'date >= current_period_start. Empty return = no owned match / guard failed.';
