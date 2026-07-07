-- card_credit_applications — per-(credit, transaction) idempotency ledger, plus
-- the idempotent rewrite of card_credit_apply_usage() (DESIGN.md §6.7; Codex
-- review 2026-07-05).
--
-- The Phase-2 ledger-bridge tap (POST /card-credits/{id}/apply, migration
-- 20260705120900) added the transaction amount to used_amount on EVERY call. So
-- applying the same transaction twice — a retry after a lost response, a
-- double-tap through stale UI, an offline-queue replay — counted the same
-- purchase toward the credit again (up to the allowance clamp). That is a
-- balance-correctness bug, not just a UX edge.
--
-- Fix: a (card_credit_id, transaction_id) idempotency ledger + a record-then-
-- increment RPC. The unique key makes a replay a no-op; the tap is now safe to
-- retry. Table + RPC ship together because the function body references the
-- table.

CREATE TABLE card_credit_applications (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    card_credit_id   uuid        NOT NULL REFERENCES card_credits(id) ON DELETE CASCADE,
    transaction_id   uuid        NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    applied_amount   numeric     NOT NULL,     -- the transaction amount counted (audit)
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- The idempotency key: a given transaction counts toward a given credit at most
-- once. A replay collides here and is a no-op — no double count.
CREATE UNIQUE INDEX card_credit_applications_credit_txn_uniq
    ON card_credit_applications (card_credit_id, transaction_id);

ALTER TABLE card_credit_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE card_credit_applications FORCE  ROW LEVEL SECURITY;

-- Owner SELECT + INSERT only. No UPDATE/DELETE (applications are immutable audit
-- rows; ON DELETE CASCADE from card_credits / transactions / auth.users handles
-- teardown). The INSERT WITH CHECK independently verifies both FK targets are
-- the caller's own — without it a direct PostgREST caller could forge an
-- application against another user's credit/transaction (the same cross-tenant
-- FK gap closed on card_credits, memory.md 2026-07-05).
CREATE POLICY card_credit_applications_select ON card_credit_applications
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY card_credit_applications_insert ON card_credit_applications
    FOR INSERT
    WITH CHECK (
        user_id = auth.uid()
        AND EXISTS (
            SELECT 1 FROM card_credits cc
             WHERE cc.id = card_credit_applications.card_credit_id
               AND cc.user_id = auth.uid()
        )
        AND EXISTS (
            SELECT 1 FROM transactions t
             WHERE t.id = card_credit_applications.transaction_id
               AND t.user_id = auth.uid()
        )
    );


-- Rewritten idempotent apply. Signature unchanged (uuid, uuid) RETURNS SETOF
-- card_credits, so CREATE OR REPLACE swaps the body (sql → plpgsql) in place and
-- keeps the existing authenticated EXECUTE grant.
CREATE OR REPLACE FUNCTION card_credit_apply_usage(
    p_credit_id uuid,
    p_transaction_id uuid
)
RETURNS SETOF card_credits
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delta numeric;
BEGIN
    -- Idempotency claim. The guards decide whether a NEW row is created: the
    -- credit + transaction must be the caller's (RLS under SECURITY INVOKER), on
    -- the same card, active, and the spend in the current period. ON CONFLICT
    -- makes a replay a no-op (v_delta stays NULL → no increment). This SELECT
    -- reads the period from the function's snapshot; the increment below carries
    -- the SAME period guard so the authoritative check happens under the row
    -- lock (see the UPDATE).
    INSERT INTO card_credit_applications (
        user_id, card_credit_id, transaction_id, applied_amount
    )
    SELECT cc.user_id, cc.id, t.id, t.amount
      FROM card_credits cc
      JOIN transactions t ON t.id = p_transaction_id
     WHERE cc.id = p_credit_id
       AND cc.status = 'active'
       AND t.status = 'active'
       AND t.card_id = cc.card_id
       AND t.date >= cc.current_period_start
    ON CONFLICT (card_credit_id, transaction_id) DO NOTHING
    RETURNING applied_amount INTO v_delta;

    -- Guarded, clamped increment. The period guard is REPEATED in this UPDATE's
    -- own WHERE (joining the transaction) so it is re-checked under the row lock:
    -- if a concurrent reset_card_credits() commits between our snapshot and the
    -- lock, EvalPlanQual re-fetches the credit row and re-evaluates
    -- `t.date >= cc.current_period_start` against the NEW (advanced) period, so an
    -- old-period spend is NOT counted into the fresh period (Codex 2026-07-05).
    -- A plain SELECT ... FOR UPDATE would NOT fix this — a plpgsql function called
    -- by one statement shares that statement's snapshot, so only the updating
    -- statement's own EvalPlanQual re-read sees the post-reset period. In that
    -- race the application row remains without a matching increment (benign: the
    -- spend is genuinely old-period; a retry is idempotent). Clamp to
    -- [0, allowance]: over-cap clamps, a refund floors at 0, a NULL allowance has
    -- no upper clamp. The row lock also serializes concurrent applies of
    -- DIFFERENT transactions with no lost update.
    IF v_delta IS NOT NULL THEN
        UPDATE card_credits cc
           SET used_amount = GREATEST(
                   0,
                   CASE WHEN cc.amount IS NULL
                        THEN cc.used_amount + v_delta
                        ELSE LEAST(cc.amount, cc.used_amount + v_delta)
                   END
               )
          FROM transactions t
         WHERE cc.id = p_credit_id
           AND cc.status = 'active'
           AND t.id = p_transaction_id
           AND t.card_id = cc.card_id
           AND t.date >= cc.current_period_start;
    END IF;

    -- Return the credit IFF an application for this (credit, transaction) exists
    -- and is the caller's: a fresh apply and an idempotent replay both return
    -- the credit (route → 200, the balance already reflects it); a guard failure
    -- or a non-owned credit returns nothing (route → 409, indistinguishable).
    RETURN QUERY
        SELECT cc.*
          FROM card_credits cc
         WHERE cc.id = p_credit_id
           AND EXISTS (
               SELECT 1 FROM card_credit_applications a
                WHERE a.card_credit_id = cc.id
                  AND a.transaction_id = p_transaction_id
           );
END;
$$;

COMMENT ON FUNCTION card_credit_apply_usage(uuid, uuid) IS
  'DESIGN.md §6.7 Phase 2 — IDEMPOTENT ledger-bridge increment for '
  'POST /card-credits/{id}/apply. Records the (credit, transaction) application '
  '(unique-keyed → replay-safe), then adds the transaction amount to used_amount '
  'clamped [0, allowance]. Returns the credit on fresh-apply OR replay; empty on '
  'guard failure / non-owned (route maps empty → 409).';
