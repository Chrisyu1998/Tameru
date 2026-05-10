-- chat_turn_trace — DESIGN.md §8.12
-- Append-only Anthropic-shaped replay log for the chat agent loop. One row
-- per /chat/turn call, storing the entire turn's message-list slice as a
-- JSONB array — including the user's typed message, every intermediate
-- (assistant_with_tool_use, user_with_tool_result) pair from the loop, and
-- the final assistant blocks.
--
-- Why this is a separate table from chat_messages (§8.11):
--
--   * chat_messages is the human-visible conversation log. UI/conversation
--     thread rendering reads from it, sees alternating user/assistant
--     rows, never sees synthetic tool_result rows.
--   * chat_turn_trace is the wire-shape replay log. The loop reads from it
--     to reconstruct the exact message sequence Claude needs on the next
--     turn (alternating user/assistant turns with tool_use blocks
--     immediately followed by tool_result blocks).
--
-- Faithful replay matters: a follow-up turn that references "that number
-- you calculated" can only ground correctly when the model sees the prior
-- tool_use + tool_result pair, not just the prose it produced (§7.2.1
-- enforces a 5-turn cap; this is the cap's source of truth).
--
-- Cap semantics: history loads ORDER BY created_at DESC LIMIT 5. With one
-- row per turn, the cap maps exactly to "5 turns" regardless of how many
-- tool hops each turn contained. seq is a deterministic tiebreaker for the
-- (rare-but-possible) case where two turns share a created_at to
-- microsecond precision.
--
-- RLS shape: same FOR ALL pattern as chat_messages. Replay state is the
-- user's own data; if the user clears their conversation, the trace goes
-- with it.

CREATE TABLE chat_turn_trace (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    conversation_id UUID        NOT NULL,
    -- Full Anthropic message list contributed by THIS turn, in wire shape:
    --   [{"role":"user","content":"<typed text>"},
    --    {"role":"assistant","content":[{"type":"tool_use",...}]},
    --    {"role":"user","content":[{"type":"tool_result",...}]},
    --    ...,
    --    {"role":"assistant","content":[{"type":"text","text":"..."}]}]
    -- Replay concatenates the messages arrays from the last 5 trace rows.
    messages        JSONB       NOT NULL,
    seq             BIGSERIAL   NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Replay reads ORDER BY created_at DESC then seq DESC (tiebreaker),
-- LIMIT 5. The composite index serves the per-conversation read directly.
CREATE INDEX chat_turn_trace_user_conv_seq_idx
    ON chat_turn_trace (user_id, conversation_id, seq DESC);

ALTER TABLE chat_turn_trace ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_turn_trace FORCE  ROW LEVEL SECURITY;

CREATE POLICY chat_turn_trace_owner ON chat_turn_trace
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
