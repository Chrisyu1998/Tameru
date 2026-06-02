-- users_meta.ui_language — Day 29 Tier 2 (DESIGN.md §6.6, §8.7).
--
-- Per-user UI / display language, the third independent i18n axis alongside
-- home_currency and timezone (DESIGN.md §6.6 — a user can want an English UI,
-- JPY currency, and Asia/Tokyo time all at once). Nullable: NULL means "no
-- explicit choice yet", and the frontend's displayLocale() falls back to the
-- browser's navigator.language, so every existing row behaves exactly as it
-- did before this migration. Mutable — unlike home_currency there is NO
-- immutability trigger; the user changes it in Settings.
--
-- Unlike timezone (a large, version-dependent IANA set validated in the app
-- layer), the supported language set is small and fixed, so a CHECK constraint
-- is the natural DB-layer guarantee — same posture as home_currency's CHECK
-- (CLAUDE.md invariant 13). The canonical set is mirrored in
-- app/util/language.py (SUPPORTED_UI_LANGUAGES) for clean 422s at the API
-- boundary; the two must stay in sync (the set is small and stable).
--
-- Drives three surfaces (DESIGN.md §6.6 Tier 2): the formatting locale that
-- displayLocale() resolves (number grouping, date layout), the chat agent's
-- reply language (chat_v12 — setting-driven, replacing chat_v11's
-- mirror-the-input), and the weekly digest's narrative + email-template
-- language. Set at /auth/bootstrap from the browser and editable via
-- PATCH /me/preferences. Owner-UPDATE RLS via the existing users_meta_owner
-- policy — no policy change needed.

ALTER TABLE users_meta
    ADD COLUMN ui_language text
        CHECK (ui_language IN ('en', 'ja', 'zh-TW'));

COMMENT ON COLUMN users_meta.ui_language IS
    'Day 29 Tier 2 — per-user UI/display language (en | ja | zh-TW). NULL = '
    'no explicit choice (frontend falls back to navigator.language). Set at '
    'bootstrap from the browser, editable in Settings; mutable, no '
    'immutability trigger. Independent of home_currency and timezone. '
    'Supported set mirrored in app/util/language.py. DESIGN.md §6.6.';
