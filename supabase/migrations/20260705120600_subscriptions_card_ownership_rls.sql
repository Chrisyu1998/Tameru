-- Enforce card ownership on subscriptions writes (parallel to the card_credits
-- fix — Codex 2026-07-05).
--
-- `subscriptions.card_id` is an FK to `cards`, but the owner-only RLS policy
-- checked only `user_id = auth.uid()`. RLS scopes the ROW's ownership, not the
-- ownership of the card the row REFERENCES — and the FK only verifies the
-- card_id exists *somewhere*. So a direct PostgREST caller (the anon key + a
-- valid user JWT both ship in the PWA bundle) could attach a subscription with
-- their own user_id to another user's card_id: a cross-tenant FK attachment + a
-- card-existence oracle, and — subscriptions-specific — the pg_cron auto-logger
-- would then mint transactions carrying that foreign card_id. Same class of
-- issue as the new card_credits table (migration 20260705120000).
--
-- Fix: add the card-ownership predicate to the write-time WITH CHECK. card_id
-- is NULLABLE here (cardless ACH bills — §8.3), so NULL is allowed. Read/delete
-- behavior is unchanged (still `user_id = auth.uid()`); only INSERT/UPDATE
-- tighten. The AF dual-write / update_card_af / soft_delete_card / auto-logger
-- are all SECURITY DEFINER (BYPASSRLS), so they are unaffected; the only
-- RLS-subject write path is `POST /subscriptions/confirm` (+ PATCH card
-- reassignment), which resolves card_ref to one of the caller's OWN cards, so
-- it satisfies the new check.
--
-- DROP + re-CREATE rather than editing 20260421120200 (already applied to prod
-- — memory.md 2026-05-22 drift rule). Same DROP+CREATE-policy approach as the
-- users_meta owner-DELETE split (20260610130000).

DROP POLICY IF EXISTS subscriptions_owner ON subscriptions;

CREATE POLICY subscriptions_owner ON subscriptions
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (
        user_id = auth.uid()
        AND (
            card_id IS NULL
            OR EXISTS (
                SELECT 1 FROM cards c
                 WHERE c.id = subscriptions.card_id
                   AND c.user_id = auth.uid()
            )
        )
    );
