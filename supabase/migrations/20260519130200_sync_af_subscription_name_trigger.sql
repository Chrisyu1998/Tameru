-- Sync companion AF subscription names on card rename — Day 19b follow-up.
--
-- The AF subscription's `name` is denormalized as
-- '{card_name} annual fee' — both insert_card_with_af (Day 19b) and
-- the (post-cancel) re-enable path in update_card_af build it from
-- cards.name at write time. autolog_subscriptions() then uses
-- subscriptions.name verbatim as the transaction's `merchant` field
-- on each anniversary.
--
-- Without this trigger, a `PATCH /cards/{id}` that changes name (or
-- any other write path that mutates cards.name) leaves the AF sub's
-- name pointing at the old card name, and next year's auto-log
-- records the old name as the merchant on the transaction. Cosmetic
-- but disorienting for the user who renamed the card weeks earlier.
--
-- A trigger is the right shape here because the AF sub's name is a
-- derived value — there's no user input to validate, no branching on
-- the route side. By contrast, AF amount and renewal-date edits flow
-- through the `update_card_af` RPC (20260519130100) because those
-- ARE user-intent cross-table changes the route validates.
--
-- The trigger is a no-op for cards without an active AF sub, and a
-- no-op when cards.name isn't changing (soft-delete UPDATEs that flip
-- status/deleted_at don't touch name).
--
-- Why not regular subscriptions: only AF subs derive their name from
-- the card. Netflix/Spotify/rent names are user-typed and unaffected
-- by card renames.
--
-- Security: SECURITY DEFINER so the trigger can update subscriptions
-- regardless of the caller's RLS posture (matches the SECURITY
-- DEFINER pattern used by the Day 19 cascade and the Day 19b
-- insert/update RPCs). The WHERE clause filters by `user_id =
-- NEW.user_id`, so the sync only ever touches rows under the same
-- ownership as the renamed card.

CREATE OR REPLACE FUNCTION sync_af_subscription_name()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Only fire on actual name changes. Soft-delete UPDATEs change
    -- status/deleted_at; the cron's `last_billing_date` advance never
    -- touches cards. `IS DISTINCT FROM` handles the NULL edge case
    -- without needing a separate clause (NULL → 'x' counts as a
    -- change; 'x' → 'x' does not).
    IF NEW.name IS DISTINCT FROM OLD.name THEN
        UPDATE subscriptions
           SET name = NEW.name || ' annual fee'
         WHERE card_id = NEW.id
           AND user_id = NEW.user_id
           AND name LIKE '% annual fee'
           AND category = 'Memberships'
           AND frequency = 'annual'
           AND status = 'active';
    END IF;
    RETURN NEW;
END;
$$;

-- Idempotent install — drop and recreate so re-applying the migration
-- against an already-running schema (dev / test) is a no-op.
DROP TRIGGER IF EXISTS cards_sync_af_subscription_name ON cards;
CREATE TRIGGER cards_sync_af_subscription_name
AFTER UPDATE OF name ON cards
FOR EACH ROW
EXECUTE FUNCTION sync_af_subscription_name();
