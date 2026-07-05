-- ai_call_log + ai_call_log_daily — add the `credit_lookup` task_type.
--
-- Phase 1 of credit tracking (DESIGN.md §6.7) adds a second card web_search
-- prompt that returns a card's list of recurring statement credits (name /
-- amount / cadence / merchant_hint). It runs on the user's JWT (invariant 14)
-- with a distinct `credit_lookup` task_type so the cost dashboard can tell it
-- apart from the multiplier `card_lookup` (both are Anthropic web_search but
-- differ in prompt and trigger). Mirrors the `recap` / `receipt_parse`
-- additions (20260703140200 / 20260704120000).

ALTER TABLE ai_call_log
    DROP CONSTRAINT IF EXISTS ai_call_log_task_type_check;
ALTER TABLE ai_call_log
    ADD CONSTRAINT ai_call_log_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest', 'recap', 'receipt_parse',
            'credit_lookup'
        ));

ALTER TABLE ai_call_log_daily
    DROP CONSTRAINT IF EXISTS ai_call_log_daily_task_type_check;
ALTER TABLE ai_call_log_daily
    ADD CONSTRAINT ai_call_log_daily_task_type_check
        CHECK (task_type IN (
            'categorization', 'chat_turn', 'memory_distill',
            'card_lookup', 'csv_import', 'digest', 'recap', 'receipt_parse',
            'credit_lookup'
        ));
