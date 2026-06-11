-- Close the default-privileges EXECUTE hole on four SECURITY DEFINER
-- functions that shipped with PUBLIC-only revokes.
--
-- Background (memory.md 2026-05-18): 20260515210000_backfill_supabase_grants.sql
-- sets `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO
-- anon, authenticated, service_role`, so every function created in `public`
-- after it receives explicit role-specific EXECUTE grants at creation time.
-- `REVOKE ... FROM PUBLIC` is a different grant tier and does not dislodge
-- those role grants — the REVOKE list must name anon and authenticated
-- explicitly.
--
-- Functions fixed here:
--
--   * autolog_subscriptions() — cron-only cross-user writer with no
--     auth.uid() guard. Its own migration comment claimed "an authenticated
--     end-user JWT cannot call it via PostgREST RPC", but the PUBLIC-only
--     revoke left it callable by anon (the public anon key shipped in the
--     PWA bundle) and authenticated. Blast radius was bounded (advisory
--     lock + partial unique index make invocations idempotent) but the
--     execution surface was unsanctioned. Now service_role-only, matching
--     the other four cron/system functions.
--
--   * soft_delete_card(uuid), insert_card_with_af(jsonb, jsonb),
--     update_card_af(uuid, numeric, boolean, date, boolean) — the GRANT to
--     authenticated is intentional (user-intent RPCs, auth.uid()-guarded
--     in every WHERE), so the only residual was anon, for whom auth.uid()
--     resolves to NULL — bounded, but anon has no business holding EXECUTE
--     on user-intent writers.

REVOKE EXECUTE ON FUNCTION autolog_subscriptions() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION autolog_subscriptions() TO service_role;

REVOKE EXECUTE ON FUNCTION soft_delete_card(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION insert_card_with_af(jsonb, jsonb) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION update_card_af(uuid, numeric, boolean, date, boolean) FROM PUBLIC, anon;
-- The three card functions keep their existing GRANT EXECUTE TO authenticated.
