-- Day 20 follow-up — defense in depth against concurrent CSV-commit duplicates.
--
-- `POST /imports/csv/commit` builds an in-memory dedup_set against
-- `active_transactions` at the start of the request, then bulk-inserts.
-- Two concurrent commits for the same file would both load an empty
-- dedup_set, both pass the in-Python check, and both insert — producing
-- duplicate ledger rows despite the documented idempotent-re-run
-- contract (DESIGN.md §5.4.3).
--
-- Single-active-device (invariant 5) prevents a second *device* from
-- being active, but it does not serialize multiple concurrent requests
-- from the same active device (two tabs, retry storm, programmatic
-- abuse). This partial unique index closes that race at the database
-- layer; the route pairs it with `.upsert(..., ignore_duplicates=True)`
-- so the race-lost rows DB-silently no-op instead of failing the batch.
--
-- Scoped to source = 'csv_import' deliberately:
--   * chat-typed inserts (source = 'nlp') can legitimately repeat the
--     same merchant + amount + date — two coffees same day same price
--     is a normal pattern and the chat path uses client_request_id for
--     idempotency, not the dedup quadruple.
--   * pg_cron auto-logger (source = 'auto_logged') has its own
--     `UNIQUE (subscription_id, date) WHERE status = 'active' AND
--     subscription_id IS NOT NULL` guard (§8.2).
-- So this index only constrains CSV-imported rows where the dedup
-- quadruple IS the contract.
--
-- The index uses the stored `merchant` column directly (not an
-- expression) because both the CSV route and the chat `/transactions/
-- confirm` route already normalize merchants via
-- `app/util/merchant.normalize_merchant` before insert. `merchant` is
-- the normalized form by convention at every write site.

CREATE UNIQUE INDEX transactions_csv_import_dedup_uniq
  ON transactions (user_id, date, merchant, amount)
  WHERE status = 'active' AND source = 'csv_import';

COMMENT ON INDEX transactions_csv_import_dedup_uniq IS
  'Day 20 — defense in depth against concurrent /imports/csv/commit '
  'duplicates. The route''s in-memory dedup_set catches the steady-state '
  'case; this index catches the race where two concurrent commits both '
  'miss the set. Scoped to source = ''csv_import'' so chat-typed and '
  'pg_cron-typed rows can legitimately repeat. See migration body for '
  'rationale.';
