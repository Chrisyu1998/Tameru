-- ai_call_log + ai_call_log_daily — re-add the `receipt_parse` task_type.
--
-- Receipt photo → transaction shipped as a v1 feature: a camera button in the
-- chat composer posts a photo to POST /receipts/parse, which runs one Gemini
-- Vision call and returns a standard transaction proposal (confirmed via the
-- existing POST /transactions/confirm with source='receipt_photo'). That
-- Vision call is logged to ai_call_log under the user's JWT (CLAUDE.md
-- invariant 14) with a distinct `receipt_parse` task_type so the cost
-- dashboard can tell receipt extraction apart from per-transaction
-- categorization (both are Gemini, but differ in trigger and token shape —
-- an image vs a short merchant string).
--
-- NOTE: this value was **removed** in 20260522130000_ai_call_log_drop_unused_enums.sql,
-- which narrowed the CHECK on the grounds that "receipt parsing is permanently
-- out of scope." That scope call was reversed — receipt photo is now v1. This
-- migration re-adds `receipt_parse` (and nothing else) to both the base table
-- and the daily rollup (which groups by task_type). Do not re-litigate "receipt
-- is out of scope" against the 20260522130000 comment: it is superseded here.

ALTER TABLE ai_call_log
    DROP CONSTRAINT IF EXISTS ai_call_log_task_type_check;
ALTER TABLE ai_call_log
    ADD CONSTRAINT ai_call_log_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest', 'recap', 'receipt_parse'
        ));

ALTER TABLE ai_call_log_daily
    DROP CONSTRAINT IF EXISTS ai_call_log_daily_task_type_check;
ALTER TABLE ai_call_log_daily
    ADD CONSTRAINT ai_call_log_daily_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest', 'recap', 'receipt_parse'
        ));
