-- Rename category 'Subscriptions' → 'Memberships' across the ledger.
--
-- DESIGN.md §6.5 disambiguation. The bucket name 'Subscriptions' collided
-- with the `subscriptions` table name (which holds both Streaming and
-- Memberships rows). Streaming media stays in 'Streaming'; the
-- non-streaming recurring bucket (software, gym, Patreon, news, cloud
-- storage) becomes 'Memberships'. Same shape Monarch uses for their
-- "Memberships and subscriptions" category.
--
-- Three sites care about the literal value, all updated atomically here:
--   1. `transactions.category` and `subscriptions.category` rows
--      currently holding 'Subscriptions' → flip to 'Memberships'. The
--      column has no CHECK constraint (categories are validated at the
--      application layer via Pydantic `ALLOWED_CATEGORIES`), so this is
--      a plain UPDATE — no constraint drop/add ceremony needed.
--   2. The `soft_delete_card(p_card_id UUID)` function's split-cascade
--      recognition triple — replaces the literal 'Subscriptions' check
--      with 'Memberships'. CREATE OR REPLACE preserves grants.
--
-- The `_is_card_af_row` recognition heuristic in app/routes/subscriptions.py
-- and the AF dual-write in app/routes/cards.py move in lockstep (the
-- same commit that adds this migration). categorize_v5 in
-- app/prompts/categorize.py records the prompt-shape change for
-- ai_call_log.prompt_hash continuity.

BEGIN;

UPDATE transactions
   SET category = 'Memberships'
 WHERE category = 'Subscriptions';

UPDATE subscriptions
   SET category = 'Memberships'
 WHERE category = 'Subscriptions';

-- Replace the function body — only the category literal in the CASE
-- predicate changes. Same SECURITY DEFINER, same `auth.uid()` filters,
-- same idempotent shape as 20260518130300.
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

COMMIT;
