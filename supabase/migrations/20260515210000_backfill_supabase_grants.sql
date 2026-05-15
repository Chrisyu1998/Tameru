-- Backfill the standard Supabase PostgREST role grants on the public schema.
--
-- The prod project was created with "Automatically expose new tables" turned
-- off. Supabase normally runs its standard GRANT block as part of that
-- toggle; with it off, every table the migrations created is reachable by
-- the postgres superuser only — PostgREST sees "permission denied" the
-- moment the anon or authenticated role touches a row.
--
-- RLS is the real security boundary in Tameru (CLAUDE.md invariant 1).
-- These grants only let PostgREST attempt the query at all; the per-table
-- RLS policies still scope rows to `auth.uid() = user_id`. With RLS enabled,
-- GRANT ALL is the Supabase-idiomatic shape — `anon` and `authenticated`
-- can only see rows their policies permit; `service_role` bypasses RLS by
-- design and needs full access for the pg_cron auto-logger and CLI
-- migrations (invariants 1, 4, 14).
--
-- The ALTER DEFAULT PRIVILEGES block extends the grants to tables that
-- future migrations create, so this is a one-time backfill rather than a
-- pattern that has to be repeated in every new migration.

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO anon, authenticated, service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON FUNCTIONS TO anon, authenticated, service_role;
