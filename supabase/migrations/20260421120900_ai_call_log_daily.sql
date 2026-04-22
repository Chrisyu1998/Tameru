-- ai_call_log_daily — DESIGN.md §8.9
-- Daily rollup of ai_call_log rows older than 90 days (§14.1). Written by the
-- pg_cron aggregator. Composite PK; no synthetic id.
--
-- user_id is NOT NULL here even though ai_call_log.user_id is nullable —
-- Postgres PRIMARY KEY columns cannot be NULL, and §8.9 declares user_id as
-- part of the composite PK. The aggregator skips ai_call_log rows with a
-- NULL user_id (system-level calls are out-of-band for per-user rollups);
-- they remain queryable via the raw table until the 90-day window expires.
--
-- Same RLS shape as ai_call_log: SELECT-only for users, all writes via the
-- service role.

CREATE TABLE ai_call_log_daily (
    date                date    NOT NULL,
    user_id             UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider            text    NOT NULL,
    model               text    NOT NULL,
    task_type           text    NOT NULL,
    sum_input_tokens    bigint  NOT NULL DEFAULT 0,
    sum_output_tokens   bigint  NOT NULL DEFAULT 0,
    count               integer NOT NULL DEFAULT 0,
    avg_latency_ms      integer,
    error_count         integer NOT NULL DEFAULT 0,
    PRIMARY KEY (date, user_id, provider, model, task_type),
    CONSTRAINT ai_call_log_daily_provider_check
        CHECK (provider IN ('anthropic', 'google', 'perplexity')),
    CONSTRAINT ai_call_log_daily_task_type_check
        CHECK (task_type IN (
            'categorization', 'nl_parse', 'chat_turn', 'memory_distill',
            'card_lookup', 'receipt_parse', 'csv_import', 'digest'
        ))
);

ALTER TABLE ai_call_log_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_call_log_daily FORCE  ROW LEVEL SECURITY;

CREATE POLICY ai_call_log_daily_owner_read ON ai_call_log_daily
    FOR SELECT
    USING (user_id = auth.uid());
