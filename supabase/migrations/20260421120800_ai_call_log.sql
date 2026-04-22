-- ai_call_log — DESIGN.md §8.8
-- Append-only audit log of every Gemini, Claude, and Perplexity API call.
-- user_id is nullable for system-level calls (background jobs, digests with no
-- user context). ON DELETE SET NULL preserves audit history across account
-- deletions.
--
-- RLS shape (revised from §8.8 original, preserves CLAUDE.md invariant 1):
--   * SELECT policy: users see their own rows.
--   * INSERT policy: users can only insert rows where user_id = auth.uid().
--     The application logger runs inside request handlers with the user's
--     JWT, not the service role. Invariant 1 stays intact.
--   * NO UPDATE or DELETE policies. A user cannot scrub their own audit
--     history. They can forge token-spend rows on their own account, which
--     is not a meaningful threat — a user attacking their own account has
--     no one to deceive but themselves.
--   * System-level callers with user_id NULL (pg_cron aggregator, future
--     digest jobs) use the service role, which bypasses RLS entirely.

CREATE TABLE ai_call_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        REFERENCES auth.users(id) ON DELETE SET NULL,
    timestamp       timestamptz NOT NULL DEFAULT now(),
    provider        text        NOT NULL,
    model           text        NOT NULL,
    task_type       text        NOT NULL,
    prompt_version  text,
    prompt_hash     text,
    input_tokens    integer     NOT NULL DEFAULT 0,
    output_tokens   integer     NOT NULL DEFAULT 0,
    latency_ms      integer,
    success         boolean     NOT NULL,
    error_code      text,
    CONSTRAINT ai_call_log_provider_check
        CHECK (provider IN ('anthropic', 'google', 'perplexity')),
    CONSTRAINT ai_call_log_task_type_check
        CHECK (task_type IN (
            'categorization', 'nl_parse', 'chat_turn', 'memory_distill',
            'card_lookup', 'receipt_parse', 'csv_import', 'digest'
        ))
);

-- Cost and debugging queries scan recent rows for a given user.
CREATE INDEX ai_call_log_user_ts_idx
    ON ai_call_log (user_id, timestamp DESC);

ALTER TABLE ai_call_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_call_log FORCE  ROW LEVEL SECURITY;

CREATE POLICY ai_call_log_owner_read ON ai_call_log
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY ai_call_log_owner_insert ON ai_call_log
    FOR INSERT
    WITH CHECK (user_id = auth.uid());
