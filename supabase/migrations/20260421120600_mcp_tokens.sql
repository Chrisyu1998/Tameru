-- mcp_tokens — DESIGN.md §8.6
-- Per-user bearer tokens for the read-only MCP server (CLAUDE.md invariant 3).
-- Plaintext token is returned to the user once; only the SHA-256 hash is
-- stored. UNIQUE(token_hash) enforces global uniqueness across users.

CREATE TABLE mcp_tokens (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash    text        NOT NULL,
    name          text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    CONSTRAINT mcp_tokens_token_hash_unique UNIQUE (token_hash)
);

ALTER TABLE mcp_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_tokens FORCE  ROW LEVEL SECURITY;

CREATE POLICY mcp_tokens_owner ON mcp_tokens
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
