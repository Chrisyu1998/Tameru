-- ai_call_log + ai_call_log_daily — drop unused provider / task_type values
-- (Day 24).
--
-- (1) provider: `perplexity` is no longer a provider as of DESIGN.md §0 —
--     card lookup moved to Claude `web_search`. The original
--     `20260421120800_ai_call_log.sql` CHECK constraint still listed all
--     three, which left a now-impossible enum value reachable via tooling.
--
-- (2) task_type: `nl_parse` and `receipt_parse` were specified in v3 of
--     DESIGN.md but no code emits them. `nl_parse` was superseded by
--     `tool_use` (CLAUDE.md invariant 8); receipt parsing is permanently
--     out of scope (CLAUDE.md "Permanently out of scope"). Narrowing the
--     CHECK constraint stops a typo on either of those two from passing
--     at write time and propagating into the rollup.
--
-- Backfill: the unused values have never been written to either table (no
-- code path emits them), so this migration does not need a data
-- migration. We simply rewrite the constraint to a narrower set.

-- ai_call_log -------------------------------------------------------------

ALTER TABLE ai_call_log
    DROP CONSTRAINT IF EXISTS ai_call_log_provider_check;
ALTER TABLE ai_call_log
    ADD CONSTRAINT ai_call_log_provider_check
        CHECK (provider IN ('anthropic', 'google'));

ALTER TABLE ai_call_log
    DROP CONSTRAINT IF EXISTS ai_call_log_task_type_check;
ALTER TABLE ai_call_log
    ADD CONSTRAINT ai_call_log_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest'
        ));

-- ai_call_log_daily -------------------------------------------------------

ALTER TABLE ai_call_log_daily
    DROP CONSTRAINT IF EXISTS ai_call_log_daily_provider_check;
ALTER TABLE ai_call_log_daily
    ADD CONSTRAINT ai_call_log_daily_provider_check
        CHECK (provider IN ('anthropic', 'google'));

ALTER TABLE ai_call_log_daily
    DROP CONSTRAINT IF EXISTS ai_call_log_daily_task_type_check;
ALTER TABLE ai_call_log_daily
    ADD CONSTRAINT ai_call_log_daily_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest'
        ));
