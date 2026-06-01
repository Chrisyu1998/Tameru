-- users_meta.timezone — Day 29 (DESIGN.md §6.6, §8.7).
--
-- Per-user IANA timezone, decoupled from home_currency and ui_language
-- (the three i18n axes are independent — DESIGN.md §6.6; a user can want an
-- English UI, JPY currency, and Asia/Tokyo time all at once). Nullable:
-- NULL means "fall back to the digest default" (America/New_York), so every
-- existing row behaves exactly as it did before this migration. Mutable —
-- unlike home_currency there is NO immutability trigger; the user changes it
-- in Settings. No CHECK constraint: the IANA zone set is large and
-- version-dependent, so validation lives in the app layer
-- (app/util/timezone.py via zoneinfo), which is the same tz database the
-- digest cron's ZoneInfo() reads when it computes per-user week bounds.
--
-- Drives the weekly digest (DESIGN.md §6.4): the cron fires hourly and sends
-- to each user at ~09:00 in THEIR timezone, and computes the summarized
-- week's Mon–Sun boundaries in that timezone. Captured at /auth/bootstrap
-- from the browser's Intl.DateTimeFormat().resolvedOptions().timeZone and
-- editable via PATCH /me/preferences. Owner-UPDATE RLS via the existing
-- users_meta_owner policy — no policy change needed.

ALTER TABLE users_meta
    ADD COLUMN timezone text;

COMMENT ON COLUMN users_meta.timezone IS
    'Day 29 — per-user IANA timezone (e.g. Asia/Tokyo). NULL = digest '
    'default (America/New_York). Set at bootstrap from the browser, '
    'editable in Settings; mutable, no immutability trigger. Independent '
    'of home_currency and ui_language. DESIGN.md §6.6.';
