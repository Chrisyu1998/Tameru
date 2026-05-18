-- conversation_distillation_state — Day 16 (DESIGN.md §7.6 layer 2)
-- One row per conversation_id, ever. The row's presence is the signal
-- "this conversation has been distilled — never distill it again." Used
-- by the piggyback predicate on POST /chat/turn to find idle, undistilled
-- conversations and schedule a BackgroundTask for distillation.
--
-- Why a separate table and not a column on a hypothetical `conversations`
-- table: DESIGN.md §8.11 deliberately keeps conversation_id as a plain
-- UUID grouper with no metadata row. Adding distillation state without
-- promoting conversation_id to an FK target lets the existing chat
-- surfaces remain unchanged.
--
-- Append-only by design: a conversation is either distilled or not.
-- There is no "re-distill" path (organic re-mentions create new
-- user_memory rows via the dedup unique index instead). The RLS shape
-- therefore exposes SELECT and INSERT only — no UPDATE, no DELETE.
-- This also means a partial-write distillation failure does NOT write
-- the row; the piggyback predicate will retry on the next turn.

CREATE TABLE conversation_distillation_state (
    conversation_id UUID        PRIMARY KEY,
    user_id         UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    distilled_at    timestamptz NOT NULL DEFAULT now()
);

-- Supports the piggyback predicate's anti-join:
--   ... WHERE NOT EXISTS (SELECT 1 FROM conversation_distillation_state
--                          WHERE conversation_id = cm.conversation_id)
-- Postgres can use the PK index for that lookup, but the (user_id,
-- distilled_at DESC) index is useful for the "show me the user's last
-- N distilled sessions" debug query and costs us nothing at v1 scale.
CREATE INDEX conversation_distillation_state_user_distilled_idx
    ON conversation_distillation_state (user_id, distilled_at DESC);

ALTER TABLE conversation_distillation_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_distillation_state FORCE  ROW LEVEL SECURITY;

CREATE POLICY conversation_distillation_state_select ON conversation_distillation_state
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY conversation_distillation_state_insert ON conversation_distillation_state
    FOR INSERT
    WITH CHECK (user_id = auth.uid());
