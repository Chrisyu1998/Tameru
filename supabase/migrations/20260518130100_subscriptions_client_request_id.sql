-- subscriptions.client_request_id — Day 19 (DESIGN.md §8.3)
--
-- The chat-confirm path (POST /subscriptions/confirm) needs offline-replay
-- idempotency. Without it, a Day 15 offline-queue drain that retries after
-- a lost response creates a duplicate subscription — and pg_cron then
-- auto-logs a duplicate transaction every billing cycle until the user
-- notices. The recovery cost grows monthly, not constantly (unlike cards,
-- where a duplicate is one delete).
--
-- Subscriptions have no natural-key partial unique index (family vs.
-- personal Netflix on the same card with the same frequency are both
-- valid; cardless subscriptions can have any shape). So `client_request_id`
-- is the only dedup defense for the chat-confirm path.
--
-- pg_cron auto-logger writes leave `client_request_id IS NULL`; the
-- partial unique index excludes those rows so the cron path is unaffected.
-- The AF dual-write from POST /cards/confirm (Day 19b) mints its own
-- crid server-side at insert time.
--
-- **Scope: non-cancelled rows only.** The route's idempotency lookup
-- (`_load_existing_by_client_request_id`) deliberately excludes
-- cancelled rows so a confirm replay after the user cancelled the
-- prior subscription creates a fresh active row — matching the §8.3
-- cancel/re-add doctrine. If this index covered cancelled rows too,
-- that replay would hit a 23505 unique-violation instead of an
-- INSERT, and the route would bubble a 500 because the retry lookup
-- still excludes the cancelled row. Mirror of the §8.2 transactions
-- partial index, which is also scoped to active rows for the same
-- reason.

ALTER TABLE subscriptions ADD COLUMN client_request_id UUID;

CREATE UNIQUE INDEX subscriptions_user_client_request_id_unique
    ON subscriptions (user_id, client_request_id)
    WHERE client_request_id IS NOT NULL AND status <> 'cancelled';
