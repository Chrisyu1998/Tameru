# Day 24 — AICallLog completeness + daily aggregation cron + Sentry

## Goal

Every Gemini, Claude, and Perplexity call writes to `ai_call_log` with all fields populated. A nightly `pg_cron` aggregator rolls 90+ day rows into `ai_call_log_daily` and deletes the originals. Sentry catches non-AI exceptions.

## Read first

- `DESIGN.md` §8.8 (`ai_call_log` fields), §8.9 (`ai_call_log_daily`), §14 (observability + ops).

## Deliverables

- Audit existing call sites — confirm every AI integration writes a row to `ai_call_log` with all required fields:
  - `provider`, `model`, `task_type`, `prompt_version`, `prompt_hash`, `input_tokens`, `output_tokens`, `latency_ms`, `success`, `error_code`.
  - Backfill any gaps: card_lookup (Day 11), entry_moment (no AI — confirm absent), digest (Day 25 — pre-wire today).
- New migration `..._aicalllog_aggregator_function.sql`:
  - `CREATE OR REPLACE FUNCTION aggregate_aicalllog() RETURNS void` that:
    1. Inserts into `ai_call_log_daily` from `ai_call_log` rows older than 90 days, grouped by `(date(timestamp), user_id, provider, model, task_type)`.
    2. `ON CONFLICT (date, user_id, provider, model, task_type) DO UPDATE` to merge if re-run.
    3. Deletes the source `ai_call_log` rows older than 90 days.
  - `SELECT cron.schedule('aggregate-aicalllog', '0 4 * * *', 'SELECT aggregate_aicalllog();');`
- Sentry:
  - Install `sentry-sdk[fastapi]`. Initialize in `app/main.py` with DSN from env.
  - Capture all non-AI exceptions (FastAPI handlers, background tasks).
  - **Do not** capture AI errors — those live in `ai_call_log`. Configure `before_send` to drop events from AI integration modules.
  - Tag events with `user_id` (from JWT) but never with transaction data.
- Admin observability:
  - `GET /admin/aicalls/summary` (gated to a configured admin user_id list) → returns last 7 days of token usage by provider + model + task. Read-only; just SQL aggregations.
  - Don't build a full admin UI today; SQL queries via Supabase dashboard are fine.
- Tests:
  - `tests/test_aicalllog_completeness.py` — for each AI integration, mock the SDK, run a call, assert one row in `ai_call_log` with all fields populated.
  - `tests/test_aggregator.py` — seed old rows, run `aggregate_aicalllog()`, assert daily rollup is correct and old rows deleted.

## Don't

- Don't double-log AI errors to Sentry. AICallLog is the source of truth.
- Don't run the aggregator cron in dev. Production only.
- Don't skip prompt_hash. It's how we detect unintentional prompt changes.

## Done when

- After a real day of usage, every AI call has a corresponding `ai_call_log` row.
- Running `aggregate_aicalllog()` on a seeded set of 91-day-old rows produces correct daily aggregates and removes the originals.
- A non-AI exception (e.g., a 500 in a route) appears in Sentry with the user_id tag.
