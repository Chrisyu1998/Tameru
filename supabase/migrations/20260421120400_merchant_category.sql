-- merchant_category — DESIGN.md §8.4
-- "Past corrections" memory for the Gemini categorization prompt. Most-recent
-- correction wins (upsert on the unique pair).

CREATE TABLE merchant_category (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    merchant    text        NOT NULL,
    category    text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT merchant_category_user_merchant_unique
        UNIQUE (user_id, merchant)
);

ALTER TABLE merchant_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_category FORCE  ROW LEVEL SECURITY;

CREATE POLICY merchant_category_owner ON merchant_category
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
