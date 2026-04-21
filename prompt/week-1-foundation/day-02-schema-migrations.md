# Day 2 — Schema + RLS via Supabase CLI migrations

## Goal

Every table from `DESIGN.md` §8 created via Supabase CLI migrations checked into the repo, with RLS enabled and policies in the same migration. Reproducible from scratch.

## Read first

- `DESIGN.md` §8 (data models), §9.1 (RLS path), §12 (migration workflow).
- `CLAUDE.md` invariants 1, 6.

## Deliverables

- `supabase/migrations/` populated with one timestamped `.sql` file per logical change. Suggested split:
  - `..._init_extensions.sql` — `CREATE EXTENSION IF NOT EXISTS pg_cron;` and any other needed extensions.
  - `..._cards.sql`
  - `..._transactions.sql` — including `UNIQUE (subscription_id, date) WHERE subscription_id IS NOT NULL`.
  - `..._subscriptions.sql`
  - `..._merchant_category.sql` — including `UNIQUE (user_id, merchant)`.
  - `..._user_memory.sql`
  - `..._mcp_tokens.sql` — including `UNIQUE (token_hash)`.
  - `..._users_meta.sql`
  - `..._ai_call_log.sql` and `..._ai_call_log_daily.sql`.
- Every table:
  - `user_id UUID NOT NULL REFERENCES auth.users(id)` (where applicable per the design — `ai_call_log.user_id` is nullable for system calls).
  - `ENABLE ROW LEVEL SECURITY;`
  - Policies for SELECT/INSERT/UPDATE/DELETE: `USING (user_id = auth.uid())` and `WITH CHECK (user_id = auth.uid())`.
- A `supabase/seed.sql` that's empty for now (placeholder for fixtures later).
- `npm install -g supabase` documented in README's "how to run locally" section, plus `supabase start` and `supabase db reset` commands.

## Don't

- Don't create any tables in the dashboard SQL editor. Migrations only.
- Don't add CRUD endpoints today — Day 5 owns transactions API.
- Don't seed fake data; demo mode is a guided tour with frontend fixtures (Day 10), not DB rows.

## Done when

- `supabase db reset` rebuilds the schema from migrations with no errors.
- Connecting to local Postgres as a non-`service_role` user shows zero rows from any user table (RLS blocks unauthenticated reads — this is a good sign).
- `supabase db diff` is empty (schema matches migrations).
