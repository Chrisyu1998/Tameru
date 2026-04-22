-- user_memory — DESIGN.md §8.5
-- Distilled cross-session facts the chat agent retrieves each turn.
-- Populated by Claude Haiku background distillation after each session.

CREATE TABLE user_memory (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    fact             text        NOT NULL,
    category         text        NOT NULL,
    relevance_score  numeric     NOT NULL DEFAULT 0.5,
    reinforced_at    timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_memory_category_check
        CHECK (category IN ('spending_pattern', 'preference', 'active_context', 'card_preference', 'goal')),
    CONSTRAINT user_memory_relevance_range
        CHECK (relevance_score >= 0 AND relevance_score <= 1)
);

-- Memory retrieval orders by relevance_score DESC per user.
CREATE INDEX user_memory_user_relevance_idx
    ON user_memory (user_id, relevance_score DESC);

ALTER TABLE user_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memory FORCE  ROW LEVEL SECURITY;

CREATE POLICY user_memory_owner ON user_memory
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
