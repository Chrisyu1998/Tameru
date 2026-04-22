-- users_meta — DESIGN.md §8.7
-- Per-user app metadata. Primary key is user_id (1:1 with auth.users), not a
-- synthetic id. active_device_id powers the single-active-device policy
-- (CLAUDE.md invariant 5, §9.1).
--
-- home_currency is chosen at signup and immutable thereafter (CLAUDE.md
-- invariant 13). v1 stores all transaction amounts in this single currency;
-- per-transaction multi-currency and FX conversion are out of scope.
-- Immutability is enforced by a BEFORE UPDATE trigger rather than a CHECK,
-- because a CHECK sees only one row and cannot compare OLD to NEW.

CREATE TABLE users_meta (
    user_id              UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    active_device_id     text,
    analytics_opted_out  boolean     NOT NULL DEFAULT false,
    home_currency        text        NOT NULL DEFAULT 'USD',
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_meta_home_currency_check
        CHECK (home_currency IN (
            'USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'SGD', 'TWD'
        ))
);

ALTER TABLE users_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE users_meta FORCE  ROW LEVEL SECURITY;

CREATE POLICY users_meta_owner ON users_meta
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE OR REPLACE FUNCTION users_meta_home_currency_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'home_currency is immutable once set (user_id=%)', OLD.user_id
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER users_meta_home_currency_immutable_trg
    BEFORE UPDATE ON users_meta
    FOR EACH ROW
    WHEN (OLD.home_currency IS DISTINCT FROM NEW.home_currency)
    EXECUTE FUNCTION users_meta_home_currency_immutable();
