# Day 24 — Observability foundation (structured logs + correlation IDs + AICallLog completeness + Sentry)

## Goal

Wire three observability surfaces so each owns one job:

1. **Structured stdout logs** (`logging` + JSON formatter) for debugging, every line carrying `correlation_id` + `user_id`, with a PII-redaction filter before emit.
2. **`ai_call_log`** for cost/audit/regression — every existing AI call site is complete (all 10 fields populated, user JWT path intact), and a nightly `pg_cron` job rolls 90+ day rows into `ai_call_log_daily` and deletes the originals.
3. **Sentry** for unhandled exceptions only — never for AI provider failures (they're already in `ai_call_log`), with one carve-out: `AICallLogError` (the audit-pipeline canary) ships.

## Read first

- `DESIGN.md` §8.8 (`ai_call_log` fields + RLS), §8.9 (`ai_call_log_daily` PK and the NULL-`user_id` skip rule, line 1034), §14.5 (the three-surfaces architecture, redaction set, log-level convention).
- `CLAUDE.md` invariants 14 (audit writes under user JWT) and 15 (three-surfaces split).
- `memory.md` 2026-05-18 "`SECURITY DEFINER` functions in `public` must REVOKE from `anon, authenticated` explicitly" (applies to `aggregate_aicalllog`).

## Deliverables

### 1. Structured logging foundation

- Add to `pyproject.toml`: `python-json-logger>=3.2`, `asgi-correlation-id>=4.3`.
- `app/logging_config.py`:
  - `configure_logging()` called from `lifespan` *before* any other startup work. Reads `LOG_LEVEL` (default `INFO`; `DEBUG` if `APP_ENV=dev`).
  - Installs one `jsonlogger.JsonFormatter` on the root logger emitting `timestamp`, `level`, `logger`, `message`, `correlation_id`, `user_id`, plus any `extra={...}` kwargs (after redaction). Uvicorn's `uvicorn.access` and `uvicorn.error` loggers route through the same formatter so the whole stream is one schema.
  - Registers `PiiRedactionFilter` (see `app/logging_redaction.py`) on the root logger. The filter walks `record.msg`, `record.args`, and any non-stdlib `record` attributes; values matching the redaction set are replaced with `<redacted:reason>` (never silently dropped — silent drops hide bugs). Redaction set per DESIGN.md §14.5: decimal-amount pattern, fields named `merchant` / `amount` / `chat_text` / `message_text` / `email` / `phone`, full card numbers (13–19 contiguous digits with optional separators), JWT-shaped tokens (`eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`), and any string starting with the service-role-key prefix.
- `app/main.py` wiring:
  - Mount `asgi_correlation_id.CorrelationIdMiddleware` **first** in the middleware stack (outermost), `header_name="X-Request-ID"`, `generator=uuid4`, `validator=is_valid_uuid4`. Echoes the id back in the response header.
  - Introduce `app/context.py` with `user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)`. Set inside `get_current_user_jwt` after successful JWT verification (`user_id_var.set(str(user.id))`); cleared in a small middleware on response so background tasks don't inherit a stale value.
  - The JSON formatter reads `correlation_id` from `asgi_correlation_id.context.correlation_id` and `user_id` from `user_id_var.get()`.

### 2. Sentry

- Already in `pyproject.toml` (`sentry-sdk[fastapi]>=2.18`). Initialize in `lifespan` with `SENTRY_DSN` from env. Add `SENTRY_DSN` to `_REQUIRED_ENV_VARS` only when `APP_ENV=production`; missing-in-dev means init is a no-op.
- Integrations: `FastApiIntegration()`, `StarletteIntegration()`, `LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)`.
- `send_default_pii=False`. `traces_sample_rate=0.0` for v1 (no APM).
- `set_user({"id": user_id_var.get()})` is set inside `get_current_user_jwt`. No email, no IP, no transaction data ever attached.
- Every event tagged with `correlation_id` (same UUID the stdout JSON record carries) via a `before_send` enrichment step.
- `before_send(event, hint)` rules — implement in `app/sentry_filters.py`, unit-tested in isolation:
  1. Drop `fastapi.HTTPException` (4xx are expected; the `internal_error` handler in `app/main.py` surfaces 5xx-shaped HTTPExceptions separately).
  2. Drop events whose top frame's module starts with one of `_AI_INTEGRATION_MODULES = ("app.integrations.gemini", "app.integrations.card_lookup", "app.agent.loop", "app.agent.memory")`.
  3. **Exception to rule 2:** if `event.exception` has `type == "AICallLogError"`, the event ships regardless. This is the audit-pipeline canary — the AI call succeeded but the `ai_call_log` INSERT itself failed; we must see it.
  4. Run `PiiRedactionFilter.redact_sentry_event(event)` over `event.request.data`, `event.request.query_string`, `event.extra`, and `event.breadcrumbs[*].data`.

### 3. AICallLog completeness audit

- For each existing call site, confirm all 10 fields are non-null and match DESIGN.md §8.8 (`provider`, `model`, `task_type`, `prompt_version`, `prompt_hash`, `input_tokens`, `output_tokens`, `latency_ms`, `success`, `error_code`). Audit set:
  - `chat_turn` — `app/agent/loop.py` (both streaming and non-streaming paths)
  - `memory_distill` — `app/agent/memory.py`
  - `categorization` and `csv_import` — `app/integrations/gemini.py`
  - `card_lookup` — `app/integrations/card_lookup.py`
- Stale-enum cleanup (one new migration, `..._ai_call_log_drop_unused_enums.sql`):
  - Drop `'perplexity'` from `provider` CHECK in both `ai_call_log` and `ai_call_log_daily` (DESIGN.md §0 — vendor removed; replacement is Claude `web_search`).
  - Drop `'nl_parse'` and `'receipt_parse'` from `task_type` CHECK in both tables (no code emits them; CLAUDE.md invariant 8 says `tool_use` replaced `nl_parse`; receipt parsing is permanently out of scope).
  - Narrow `Literal[...]` in `app/integrations/aicalllog.py` to match.
  - Touch `DESIGN.md` §8.8 line 995 parenthetical and §8.8 / §8.9 CHECK lists.

### 4. Aggregator (pg_cron)

- New migration `..._aicalllog_aggregator_function.sql`:
  - `CREATE OR REPLACE FUNCTION aggregate_aicalllog() RETURNS void LANGUAGE plpgsql SECURITY DEFINER`. In one implicit transaction:
    1. `INSERT INTO ai_call_log_daily (date, user_id, provider, model, task_type, sum_input_tokens, sum_output_tokens, count, avg_latency_ms, error_count) SELECT date(timestamp), user_id, provider, model, task_type, SUM(input_tokens), SUM(output_tokens), COUNT(*), AVG(latency_ms)::int, SUM(CASE WHEN success THEN 0 ELSE 1 END) FROM ai_call_log WHERE timestamp < now() - interval '90 days' AND user_id IS NOT NULL GROUP BY date(timestamp), user_id, provider, model, task_type ON CONFLICT (date, user_id, provider, model, task_type) DO NOTHING;`
    2. `DELETE FROM ai_call_log WHERE timestamp < now() - interval '90 days' AND user_id IS NOT NULL;`
  - Idempotency: the function is single-pass. After step 2 the source rows are gone, so a re-run on the same day finds nothing to aggregate. `ON CONFLICT DO NOTHING` covers the (rare) double-fire window where step 1 wrote but step 2 hadn't yet.
  - System-level rows (`user_id IS NULL`) are intentionally **never** aggregated (§8.9 line 1034 — composite PK forbids NULL). They remain queryable in `ai_call_log` past 90 days. Document this in a comment block at the top of the migration.
  - `REVOKE EXECUTE ON FUNCTION aggregate_aicalllog() FROM PUBLIC, anon, authenticated; GRANT EXECUTE ON FUNCTION aggregate_aicalllog() TO service_role;` (per the 2026-05-18 SECURITY DEFINER privilege rule).
- Scheduling — **separate** migration `..._aicalllog_aggregator_schedule.sql`:
  - Body wraps in `DO $$ BEGIN IF current_setting('app.environment', true) = 'production' THEN PERFORM cron.schedule('aggregate-aicalllog', '15 4 * * *', 'SELECT aggregate_aicalllog();'); END IF; END $$;`.
  - `app.environment` is set to `'production'` only on the live Supabase project (Dashboard → Database → Custom Postgres Config). Local Supabase leaves it unset; the schedule no-ops. The migration itself still applies cleanly everywhere.
  - Cron timing `'15 4 * * *'` (04:15 UTC) — spaced after `autolog_subscriptions` at `'0 4 * * *'` and `prune_user_memory` at `'0 3 * * *'` so `cron.job_run_details` contention is sequential.

### 5. Admin observability surface

- `GET /admin/aicalls/summary?days=7` (read-only, no UI today). Returns last 7 days of token usage grouped by `(provider, model, task_type)` with `count`, `sum_input_tokens`, `sum_output_tokens`, `error_count`. Reads `ai_call_log` directly (a 7-day query is always inside the 90-day hot window).
- Admin gating: parse `ADMIN_USER_IDS` (comma-separated UUIDs) from env at boot into a `frozenset[UUID]`. `require_admin` dependency 404s (not 403 — minimize surface disclosure) any non-admin caller. `ADMIN_USER_IDS` is *not* in `_REQUIRED_ENV_VARS`: an empty set means the route exists but admits no one — acceptable until a real admin is configured.

### 6. Tests

- `tests/test_aicalllog_completeness.py` — one structural test that imports every module known to call `log_ai_call` (`app.agent.loop`, `app.agent.memory`, `app.integrations.gemini`, `app.integrations.card_lookup`), exercises each path through fixture users with mocked SDK clients, and for each: asserts (a) exactly one row inserted, (b) all 10 fields populated, (c) `user_id` matches the calling JWT's `auth.uid()` (invariant 14). Re-use existing per-integration fixtures; do not duplicate.
- `tests/test_aggregator.py` — seed: rows at 95 days old (regular user), 89 days old (regular user), 95 days old (`user_id IS NULL`). Run `aggregate_aicalllog()`. Assert (a) 95-day non-null rows produce correct daily rollups with `sum_input_tokens` / `sum_output_tokens` / `count` / `avg_latency_ms` / `error_count` math correct, (b) 89-day rows unchanged in `ai_call_log` and absent from `ai_call_log_daily`, (c) NULL-user-id rows unchanged in `ai_call_log` and absent from `ai_call_log_daily`, (d) 95-day non-null rows deleted from `ai_call_log`, (e) second `aggregate_aicalllog()` call produces no error and no duplicates.
- `tests/test_logging_pii_redaction.py` — feed crafted log records covering every redaction pattern (amount, merchant, chat text, email, phone, full card number, JWT, service-role key). Assert the JSON-serialized formatter output contains `<redacted:reason>` in place of each value and never the raw value. Include a positive control (a legitimate log line that should pass through intact).
- `tests/test_sentry_before_send.py` — pure unit test of `before_send`. Feed crafted Sentry-shaped event dicts: (a) 4xx HTTPException → returns `None`, (b) non-AI exception in a route handler → returns event (with redacted request body), (c) AI-module exception that is *not* `AICallLogError` → returns `None`, (d) `AICallLogError` from `app.integrations.gemini` → returns event (canary path), (e) event with raw transaction amount in `extra` → returns event with redacted `extra`.
- `tests/test_correlation_id_threading.py` — single request through a route emits log records that all carry the same `correlation_id` and `user_id`, and the response carries the same id in the `X-Request-ID` header.

## Don't

- **Don't drop `AICallLogError` in Sentry's filter.** It is exactly the audit-pipeline canary that must stay visible.
- Don't double-log AI provider failures to Sentry. `ai_call_log` is the source of truth for AI failures (DESIGN.md §14.2).
- Don't run the aggregator schedule in dev. The `app.environment` guard in migration #4 is how that's enforced — do not work around it.
- Don't log request bodies or response bodies. Use `extra={...}` with a whitelisted field set instead.
- Don't add a separate logging vendor (Datadog, Logtail, Better Stack) at v1 scale. Railway stdout + Sentry is the supported stack.
- Don't skip `prompt_hash`. It's how we detect unintentional prompt drift (DESIGN.md §11).
- Don't introduce `structlog`. Stdlib `logging` + `python-json-logger` is the v1 choice; swap later if needed.

## Done when

- A request through any route emits log lines that all share one `correlation_id` and one `user_id`, parseable as JSON in the Railway log viewer (`jq` works).
- The response from any route includes the `X-Request-ID` header carrying the same id.
- A real non-AI 500 from a route handler appears in Sentry, tagged with `user_id` and `correlation_id`, with the request body redacted.
- `AICallLogError` raised inside `app/integrations/gemini.py` appears in Sentry (canary path open).
- An ordinary `RuntimeError` raised inside `app/integrations/gemini.py` does NOT appear in Sentry (filter path closed).
- A log line containing `email=user@example.com` or a decimal amount in its message or `extra` reads `<redacted:email>` / `<redacted:amount>` in stdout, never the raw value.
- `aggregate_aicalllog()` against the Day-24 seeded set produces correct daily rollups, deletes only 95-day non-null rows, leaves NULL-user-id rows in place, and is idempotent on re-run.
- `GET /admin/aicalls/summary?days=7` returns sensible numbers for a configured admin user; returns 404 for any non-admin (including unauthenticated callers).
- After a real day of usage, every AI call has a corresponding `ai_call_log` row with all 10 fields populated and `user_id` correctly attributed.
