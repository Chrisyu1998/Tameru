-- goals — DESIGN.md §8.13, Day 9b
-- Per-user spending budgets keyed on (user_id, category, period). Latest-wins
-- enforced at the schema layer via a unique constraint plus an upsert in the
-- `set_goal` agent tool — the verb "set" implies overwrite, and any reader
-- ("what's my Dining/month budget?") must return at most one row without
-- application-level tiebreaking. NULL category encodes "overall budget across
-- categories," which requires NULLS NOT DISTINCT so two such rows collapse to
-- one. Without it, Postgres's default unique-constraint semantics treat NULLs
-- as distinct and the overall-budget slot would silently duplicate.
--
-- A named CONSTRAINT (not just a UNIQUE INDEX) is required because PostgREST's
-- upsert routes its `on_conflict="user_id,category,period"` query parameter to
-- a named constraint by column list — a bare functional index expression
-- (e.g. COALESCE(category, '')) can't be referenced that way.

CREATE TABLE goals (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    category    text,
    amount      numeric     NOT NULL CHECK (amount > 0),
    period      text        NOT NULL CHECK (period IN ('week', 'month', 'year')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT goals_user_cat_period_uniq
        UNIQUE NULLS NOT DISTINCT (user_id, category, period)
);

CREATE INDEX goals_user_idx ON goals (user_id);

ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals FORCE  ROW LEVEL SECURITY;

CREATE POLICY goals_owner_all ON goals
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- Keep updated_at honest on UPDATE. Without this, a PostgREST upsert that
-- routes to the DO UPDATE path leaves updated_at frozen at the original
-- INSERT's now(), defeating any "most recently changed" diagnostics.
CREATE OR REPLACE FUNCTION goals_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER goals_set_updated_at
    BEFORE UPDATE ON goals
    FOR EACH ROW
    EXECUTE FUNCTION goals_set_updated_at();
