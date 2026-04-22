-- subscriptions — DESIGN.md §8.3
-- Recurring charges; pg_cron auto-logger (§14.3) scans active rows by
-- next_billing_date each day.

CREATE TABLE subscriptions (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    card_id            UUID        NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    name               text        NOT NULL,
    amount             numeric     NOT NULL,
    frequency          text        NOT NULL,
    start_date         date        NOT NULL,
    next_billing_date  date        NOT NULL,
    category           text        NOT NULL,
    status             text        NOT NULL DEFAULT 'active',
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT subscriptions_frequency_check
        CHECK (frequency IN ('monthly', 'quarterly', 'annual', 'weekly')),
    CONSTRAINT subscriptions_status_check
        CHECK (status IN ('active', 'paused', 'cancelled'))
);

-- Partial index powering the daily auto-logger scan.
-- Grows with the count of *active* subscriptions, not total history.
CREATE INDEX subscriptions_active_due_idx
    ON subscriptions (next_billing_date)
    WHERE status = 'active';

ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE  ROW LEVEL SECURITY;

CREATE POLICY subscriptions_owner ON subscriptions
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
