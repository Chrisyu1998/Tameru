-- transactions.client_request_id — DESIGN.md §8.2
-- Chat-confirm idempotency for offline-replay.
--
-- The client (propose_transaction tool, Day 9) generates a UUID at proposal
-- time and carries it through to POST /transactions/confirm. A reconnect
-- that drains the IndexedDB queue twice (Day 15) must not produce duplicate
-- rows. The partial unique index enforces that at the DB layer — the Day 5
-- handler catches the 23505 and returns the existing row with insight: null.
--
-- Nullable column + WHERE NOT NULL partial index: the pg_cron subscription
-- auto-logger (Day 19) and CSV import (Day 20) write at the SQL layer with
-- no client_request_id, and must not be forced to mint one.

ALTER TABLE transactions
    ADD COLUMN client_request_id UUID;

CREATE UNIQUE INDEX transactions_user_client_request_id_unique
    ON transactions (user_id, client_request_id)
    WHERE client_request_id IS NOT NULL;
