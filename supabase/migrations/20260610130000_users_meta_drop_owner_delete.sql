-- users_meta: drop owner DELETE from RLS (2026-06 audit P3-9).
--
-- The original `users_meta_owner` policy was FOR ALL, and the
-- home_currency immutability trigger is BEFORE UPDATE only — so a user
-- (or a compromised JWT) could DELETE their own users_meta row via direct
-- PostgREST and re-bootstrap with a different currency: a two-statement
-- mutation of the immutable home_currency while the ledger amounts stayed
-- denominated in the old one. Exactly the corruption CLAUDE.md invariant
-- 13 exists to prevent.
--
-- Fix: split the policy into explicit SELECT / INSERT / UPDATE policies
-- and grant no DELETE. The row now dies only via the auth.users ON DELETE
-- CASCADE — i.e. real account deletion, which goes through the Supabase
-- auth admin API (service role, RLS-bypassing), so invariant 13's
-- documented escape hatch ("delete account and re-signup") is unaffected.
-- No app code path issues a users_meta DELETE (bootstrap is INSERT-only,
-- preferences are PATCH), so this is invisible to the product.

DROP POLICY users_meta_owner ON users_meta;

CREATE POLICY users_meta_owner_select ON users_meta
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY users_meta_owner_insert ON users_meta
    FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY users_meta_owner_update ON users_meta
    FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- No DELETE policy: under FORCE ROW LEVEL SECURITY, the absence of a
-- policy for a command denies it outright.
