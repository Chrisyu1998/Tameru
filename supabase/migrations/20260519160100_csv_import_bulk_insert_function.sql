-- Day 20 follow-up — bulk INSERT entrypoint for /imports/csv/commit.
--
-- WHY THIS FUNCTION EXISTS
--
-- The Day 20 commit route does a per-batch bulk insert and wants the
-- companion partial unique index `transactions_csv_import_dedup_uniq`
-- (migration 20260519160000) to drop concurrent-commit duplicates at
-- the DB layer via `ON CONFLICT (...) DO NOTHING`. The catch:
--
--   * PostgREST's `on_conflict` upsert parameter only knows the column
--     list; it cannot pass the partial-index WHERE predicate.
--   * Without the matching predicate Postgres refuses the inference
--     with 42P10 "there is no unique or exclusion constraint matching
--     the ON CONFLICT specification".
--
-- This function emits the matching `WHERE status = 'active' AND
-- source = 'csv_import'` directly so Postgres infers the partial index
-- and silently skips race-lost rows. The route calls it via
-- `client.rpc("csv_import_bulk_insert", {"p_rows": <jsonb_array>})`
-- and reads the returning set to learn which rows actually landed.
--
-- SECURITY POSTURE
--
-- SECURITY INVOKER so the function runs as the caller and `auth.uid()`
-- resolves to the JWT subject. The INSERT hardcodes
-- `user_id := auth.uid()` so a tampered client cannot attribute a
-- row to another user — defense in depth beside the RLS policy that
-- enforces `user_id = auth.uid()` on writes.
--
-- The row payload (jsonb) is whitelist-projected into the typed
-- columns. Extra keys are ignored. Missing keys surface as NULLs
-- which the column constraints then catch (e.g. `merchant NOT NULL`).
-- No untyped string interpolation: every value flows through a
-- ::numeric / ::date / ::uuid cast.

CREATE OR REPLACE FUNCTION csv_import_bulk_insert(p_rows jsonb)
RETURNS SETOF transactions
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  RETURN QUERY
  INSERT INTO transactions (
    user_id,
    card_id,
    merchant,
    amount,
    date,
    category,
    source,
    gemini_suggestion
  )
  SELECT
    auth.uid(),
    NULLIF(r->>'card_id', '')::uuid,
    r->>'merchant',
    (r->>'amount')::numeric,
    (r->>'date')::date,
    r->>'category',
    r->>'source',
    r->>'gemini_suggestion'
  FROM jsonb_array_elements(p_rows) AS r
  ON CONFLICT (user_id, date, merchant, amount)
    WHERE status = 'active' AND source = 'csv_import'
  DO NOTHING
  RETURNING *;
END;
$$;

COMMENT ON FUNCTION csv_import_bulk_insert(jsonb) IS
  'Day 20 — bulk INSERT for /imports/csv/commit with concurrent-race '
  'guard via the partial unique index transactions_csv_import_dedup_uniq. '
  'Returns only the rows that actually landed; the route reads this to '
  'reconcile its in-Python skipped_duplicates counter. Hardcodes '
  'user_id := auth.uid() so a tampered client cannot mis-attribute. '
  'See migration body for the PostgREST 42P10 workaround rationale.';

-- PostgREST exposes RPC to all authenticated callers by default; no
-- separate GRANT is needed (the default-privilege backfill in
-- 20260515210000_backfill_supabase_grants.sql gives `authenticated`
-- EXECUTE on every function created in `public`). Since this is a
-- SECURITY INVOKER function, we deliberately do NOT REVOKE — the RLS
-- check fires per row through `auth.uid()`.
