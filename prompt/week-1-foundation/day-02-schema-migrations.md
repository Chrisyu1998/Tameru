# Day 2 — Schema + RLS via Supabase CLI migrations

## Goal

Every table from `DESIGN.md` §8 created via Supabase CLI migrations checked into the repo, with RLS enabled and policies in the same migration. Reproducible from scratch on a fresh machine with `supabase db reset`.

## Read first

- `DESIGN.md` §8 (data models), §9.1 (RLS path), §12 (migration workflow), §14.3 (pg_cron jobs — informs the `subscriptions` index).
- `CLAUDE.md` invariants 1, 6.

## Deliverables

### Migrations

`supabase/migrations/` populated with one timestamped `.sql` file per logical change. Suggested split:

- `..._init_extensions.sql`
  - `CREATE EXTENSION IF NOT EXISTS pgcrypto;` — needed for `gen_random_uuid()`.
  - `CREATE EXTENSION IF NOT EXISTS pg_cron;` — used from Day 5+ for the subscription auto-logger and Day 4+ for the AICallLog aggregator.
- `..._cards.sql`
- `..._transactions.sql` — including:
  - `UNIQUE (subscription_id, date) WHERE subscription_id IS NOT NULL`.
  - Index `(user_id, date DESC)` — dashboard and chat queries sort by recent.
  - `BEFORE UPDATE` trigger that sets `updated_at = now()` (used for offline sync conflict resolution, §8.2).
- `..._subscriptions.sql` — including:
  - Partial index `(status, next_billing_date) WHERE status = 'active'` — the `pg_cron` auto-logger (§14.3) scans this daily; without it, scan cost grows with total subscriptions instead of active ones.
- `..._merchant_category.sql` — including `UNIQUE (user_id, merchant)`.
- `..._user_memory.sql` — including index `(user_id, relevance_score DESC)` for memory retrieval.
- `..._mcp_tokens.sql` — including `UNIQUE (token_hash)`.
- `..._users_meta.sql`.
- `..._ai_call_log.sql` — including index `(user_id, timestamp DESC)` for cost queries.
- `..._ai_call_log_daily.sql`.

### Every user-owned table (cards, transactions, subscriptions, merchant_category, user_memory, mcp_tokens, users_meta)

- `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`.
  - CASCADE is deliberate: account deletion must clean up user data (GDPR / user deletion requests). RESTRICT would make deletion fail.
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` (except `users_meta`, whose PK is `user_id`).
- `created_at timestamptz NOT NULL DEFAULT now()`.
- `ENABLE ROW LEVEL SECURITY;` **and** `ALTER TABLE … FORCE ROW LEVEL SECURITY;` — FORCE makes RLS apply to the table owner too, closing a quiet bypass.
- Exactly one policy per table, `FOR ALL`:

  ```sql
  CREATE POLICY {table}_owner ON {table}
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
  ```

  Rationale: `FOR ALL` is one statement per table instead of four, which prevents accidental asymmetry between SELECT/INSERT/UPDATE/DELETE. The service role bypasses RLS automatically — pg_cron and migrations need no special policies.

### `ai_call_log` and `ai_call_log_daily` — audit tables, different policy shape

These tables are append-only audit logs. Users must not be able to forge or scrub their own entries.

- `user_id UUID NULL REFERENCES auth.users(id) ON DELETE SET NULL` — nullable for system-level calls (§8.8); SET NULL preserves audit history after account deletion.
- `ENABLE ROW LEVEL SECURITY;` + `FORCE ROW LEVEL SECURITY;`.
- **`SELECT`-only policy**, not `FOR ALL`:

  ```sql
  CREATE POLICY ai_call_log_owner_read ON ai_call_log
    FOR SELECT
    USING (user_id = auth.uid());
  ```

  No `INSERT`/`UPDATE`/`DELETE` policies. All writes come from the backend logger and the `pg_cron` aggregator, both using the service role. A compromised user JWT cannot tamper with the audit trail.

### Enum-like fields — CHECK constraints

Add `CHECK` constraints on text fields whose values are enumerated in `DESIGN.md` §8. Add them now; retrofitting requires cleaning bad values.

- `transactions.source IN ('manual','nlp','receipt_photo','auto_logged','csv_import')`.
- `subscriptions.frequency IN ('monthly','quarterly','annual','weekly')`.
- `subscriptions.status IN ('active','paused','cancelled')`.
- `user_memory.category IN ('spending_pattern','preference','active_context','card_preference','goal')`.
- `ai_call_log.provider IN ('anthropic','google','perplexity')`.
- `ai_call_log.task_type IN ('categorization','nl_parse','chat_turn','memory_distill','card_lookup','receipt_parse','csv_import','digest')`.
- `cards.program IN ('UR','MR','TYP','Bilt','Other')`.

### Seed

- `supabase/seed.sql` — empty for now (placeholder for fixtures later). Invariant 10: demo mode is frontend fixtures, not DB rows.

### README — "how to run locally" section

- Install the Supabase CLI. Prefer `brew install supabase/tap/supabase` on macOS; `npm install -g supabase` as the cross-platform fallback. Note that Docker Desktop must be running for `supabase start`.
- `supabase start` — boots local Postgres + Auth.
- `supabase db reset` — drops and rebuilds the schema from `supabase/migrations/` + `seed.sql`.
- `supabase db diff -f <name>` — the workflow for generating the next migration (§12).

## Don't

- Don't create any tables in the dashboard SQL editor. Migrations only (invariant 6).
- Don't add CRUD endpoints today — Day 5 owns the transactions API.
- Don't seed fake data; demo mode is a guided tour with frontend fixtures (Day 10), not DB rows (invariant 10).
- Don't write `INSERT/UPDATE/DELETE` policies on `ai_call_log` or `ai_call_log_daily`. Users read their own rows; writes are service-role only.
- Don't schedule any `pg_cron` jobs today — the extension is installed, but the first scheduled job lands with its feature (Day 4 aggregator, Day 5+ subscription auto-logger).

## Done when

- `supabase db reset` rebuilds the schema from migrations with zero errors on a fresh machine.
- Connecting to local Postgres as an authenticated (non-`service_role`) user with no JWT claims shows zero rows from any user-owned table — RLS blocks it.
- A quick sanity check as authenticated user A cannot `INSERT` an `ai_call_log` row claiming `user_id = auth.uid()` — the missing write policy rejects it. (Service role can; confirm by inserting one via `psql` as `postgres` with RLS bypassed.)
- `FORCE ROW LEVEL SECURITY` is set on every table (`SELECT relname, relforcerowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace` shows `t` for all app tables).
- Every `user_id` FK cascades on user delete: `SELECT conname, confdeltype FROM pg_constraint WHERE contype='f' AND confrelid='auth.users'::regclass` shows `c` (CASCADE) for user-owned tables and `n` (SET NULL) for `ai_call_log`/`ai_call_log_daily`.
