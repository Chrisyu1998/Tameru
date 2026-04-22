-- transactions — DESIGN.md §8.2
-- Core ledger. Supports manual entry, NL parse, CSV import, receipt photo,
-- and pg_cron subscription auto-log. updated_at powers offline sync conflict
-- resolution (§10.1).

CREATE TABLE transactions (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    card_id            UUID        REFERENCES cards(id)         ON DELETE SET NULL,
    subscription_id    UUID        REFERENCES subscriptions(id) ON DELETE SET NULL,
    merchant           text        NOT NULL,
    amount             numeric     NOT NULL,
    date               date        NOT NULL,
    category           text        NOT NULL,
    gemini_suggestion  text,
    source             text        NOT NULL,
    notes              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT transactions_source_check
        CHECK (source IN ('manual', 'nlp', 'receipt_photo', 'auto_logged', 'csv_import'))
);

-- Idempotency for the subscription auto-logger. §14.3: INSERT ... ON CONFLICT
-- DO NOTHING relies on this partial unique index.
CREATE UNIQUE INDEX transactions_subscription_date_unique
    ON transactions (subscription_id, date)
    WHERE subscription_id IS NOT NULL;

-- Dashboard + chat "recent activity" queries sort by date DESC.
CREATE INDEX transactions_user_date_idx
    ON transactions (user_id, date DESC);

-- Offline sync conflict resolution needs updated_at to advance on every UPDATE.
-- Trigger ensures the column moves forward even if a caller forgets to set it.
CREATE OR REPLACE FUNCTION transactions_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER transactions_set_updated_at
    BEFORE UPDATE ON transactions
    FOR EACH ROW
    EXECUTE FUNCTION transactions_set_updated_at();

ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions FORCE  ROW LEVEL SECURITY;

CREATE POLICY transactions_owner ON transactions
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
