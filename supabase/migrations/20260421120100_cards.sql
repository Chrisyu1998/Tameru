-- cards — DESIGN.md §8.1
-- User-owned credit/debit cards with category multipliers.

CREATE TABLE cards (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name          text        NOT NULL,
    issuer        text        NOT NULL,
    program       text        NOT NULL,
    multipliers   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    annual_fee    numeric,
    last_four     text,
    color         text,
    source_urls   text[]      NOT NULL DEFAULT '{}',
    active        boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cards_program_check
        CHECK (program IN ('UR', 'MR', 'TYP', 'Bilt', 'Other'))
);

ALTER TABLE cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE cards FORCE  ROW LEVEL SECURITY;

CREATE POLICY cards_owner ON cards
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
