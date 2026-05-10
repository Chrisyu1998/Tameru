-- chat_messages — DESIGN.md §8.11
-- Persistent chat history for the Claude Haiku agent loop (§7.1, §7.6 layer 1).
-- One row per turn (user input or assistant final), each storing the full
-- Anthropic content-block sequence as JSONB so the loop can replay tool_use /
-- tool_result blocks exactly as they were emitted on the next turn —
-- flattening to a single string would lose the structure Claude expects back.
--
-- conversation_id is a plain UUID grouper, not an FK to a separate
-- `conversations` table. v1 has no per-conversation metadata (title,
-- archived, shared); the grouper alone is enough. Promote later if
-- conversation-level metadata becomes load-bearing.
--
-- RLS shape: same FOR ALL pattern as `transactions` (§8). Chat content is
-- the user's own data — they read, write, update, and delete their own
-- rows. Audit-style INSERT-only would block a future "clear conversation"
-- feature for no v1 benefit.

CREATE TABLE chat_messages (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    conversation_id UUID        NOT NULL,
    role            text        NOT NULL,
    content_blocks  JSONB       NOT NULL,
    -- Monotonic insertion-order tiebreaker. created_at alone is not enough:
    -- a single turn writes user + assistant rows in one batched insert,
    -- which share `now()` to microsecond precision in the same transaction.
    -- Loading them back via ORDER BY created_at then returns them in
    -- non-deterministic order; Anthropic rejects an [assistant, user]
    -- replay on the next turn (alternation is required). seq makes the
    -- ordering unambiguous regardless of insert batching.
    seq             BIGSERIAL   NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chat_messages_role_check
        CHECK (role IN ('user', 'assistant'))
);

-- History loads in (user_id, conversation_id, seq ASC) order. The
-- composite index serves both the per-user scope and the conversation-grouped
-- read; the trailing seq avoids a sort when paginating long histories.
CREATE INDEX chat_messages_user_conv_seq_idx
    ON chat_messages (user_id, conversation_id, seq);

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages FORCE  ROW LEVEL SECURITY;

CREATE POLICY chat_messages_owner ON chat_messages
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
