-- users_meta — DESIGN.md §8.7
-- Per-user app metadata. Primary key is user_id (1:1 with auth.users), not a
-- synthetic id. active_device_id powers the single-active-device policy
-- (CLAUDE.md invariant 5, §9.1).

CREATE TABLE users_meta (
    user_id              UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    active_device_id     text,
    analytics_opted_out  boolean     NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE users_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE users_meta FORCE  ROW LEVEL SECURITY;

CREATE POLICY users_meta_owner ON users_meta
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
