-- ai_call_log + ai_call_log_daily — add the `recap` task_type (DESIGN.md §6.4).
--
-- The in-app weekly recap (GET /chat/recap) composes the digest payload on
-- demand under the user's JWT for users who have no cron-stored recap yet
-- (digest-disabled users, or before Monday's cron fires). That Sonnet call is
-- logged to ai_call_log under the user's JWT (CLAUDE.md invariant 14) with a
-- distinct `recap` task_type — kept separate from the cron's `digest` sends so
-- the cost dashboard can tell on-demand recap composes apart from scheduled
-- email digests (they share the same model + prompt but differ in trigger and
-- billing shape). The daily rollup groups by task_type, so widening both
-- CHECKs is all that's needed for `recap` to flow through.

ALTER TABLE ai_call_log
    DROP CONSTRAINT IF EXISTS ai_call_log_task_type_check;
ALTER TABLE ai_call_log
    ADD CONSTRAINT ai_call_log_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest', 'recap'
        ));

ALTER TABLE ai_call_log_daily
    DROP CONSTRAINT IF EXISTS ai_call_log_daily_task_type_check;
ALTER TABLE ai_call_log_daily
    ADD CONSTRAINT ai_call_log_daily_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest', 'recap'
        ));
