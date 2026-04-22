-- ai_call_log — DESIGN.md §8.8
-- Append-only audit log of every Gemini, Claude, and Perplexity API call.
-- user_id is nullable for system-level calls (background jobs, digests with no
-- user context). ON DELETE SET NULL preserves audit history across account
-- deletions.
--
-- RLS shape intentionally differs from user-owned tables:
--   * SELECT policy so users can see their own rows if we expose a usage view.
--   * NO INSERT/UPDATE/DELETE policies. All writes are made by the backend
--     logger and the pg_cron aggregator using the service role, which bypasses
--     RLS. A compromised user JWT cannot forge or scrub audit entries.

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
