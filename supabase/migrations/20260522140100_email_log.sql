-- email_log — Day 25 (DESIGN.md §6.4, §8.14).
--
-- Per-send record for scheduled email. v1 ships one kind ('digest');
-- extending to welcome-sequence kinds is a one-line CHECK widening when
-- §16 ships — no schema change otherwise.
--
-- IDEMPOTENCY: the partial unique index `email_log_weekly_dedup` on
-- (user_id, kind, date_trunc('week', sent_at)) WHERE success is the
-- load-bearing primitive that makes the cron safe to re-run on the same
-- Monday (manual rerun, Railway worker recycle, mid-run restart). The
-- partial-on-success predicate is deliberate: a transient Resend 5xx
-- writes a success=false row that does NOT lock the user out of a retry
-- within the same week. A naive (user_id, kind, week) full unique index
-- would have the opposite semantics — a single failure suppresses the
-- whole week for that user — which is the wrong tradeoff.
--
-- RLS: enabled with NO policies. Service role only (DESIGN.md §8.14;
-- same posture as stripe_events §8.10). v1 has no user-facing read
-- surface; a future "show me my email history" Settings panel would
-- add a narrow owner-SELECT policy then.

CREATE TABLE email_log (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    kind                text        NOT NULL,
    sent_at             timestamptz NOT NULL DEFAULT now(),
    success             boolean     NOT NULL,
    provider_message_id text,
    error_code          text,
    bounce_type         text,
    CONSTRAINT email_log_kind_check
        CHECK (kind IN ('digest')),
    CONSTRAINT email_log_bounce_type_check
        CHECK (bounce_type IS NULL OR bounce_type IN ('hard', 'soft', 'complaint'))
);

-- Weekly idempotency: only successful sends count. A failed send leaves
-- success=false rows that don't block a retry; a successful send blocks
-- any further send for that user/kind/week.
--
-- IMMUTABILITY: the index expression must be IMMUTABLE. The 2-arg
-- date_trunc(text, timestamptz) is STABLE (it reads the session
-- timezone implicitly) and Postgres rejects it in an index expression.
-- The 3-arg date_trunc(text, timestamptz, text) is reportedly marked
-- IMMUTABLE on some Postgres builds (it is on local Supabase) but
-- STABLE on others — relying on it is fragile across server versions.
-- The bulletproof workaround is to cast `sent_at` to `timestamp`
-- (without timezone) via `AT TIME ZONE 'UTC'` first: the cast with a
-- literal timezone string is IMMUTABLE, and `date_trunc(text,
-- timestamp)` is IMMUTABLE in every Postgres version. UTC is the right
-- reference here — week boundaries are a system invariant, not a
-- user-locale concern. The RPC and the cron's reservation logic use
-- the same expression so the partial-index inference matches.
CREATE UNIQUE INDEX email_log_weekly_dedup
    ON email_log (
        user_id,
        kind,
        date_trunc('week', sent_at AT TIME ZONE 'UTC')
    )
    WHERE success;

-- Webhook lookup by provider message id (Resend's id field).
CREATE INDEX email_log_provider_message_id
    ON email_log (provider_message_id)
    WHERE provider_message_id IS NOT NULL;

ALTER TABLE email_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_log FORCE  ROW LEVEL SECURITY;
-- Deliberately no policies. Service-role-only access.

COMMENT ON TABLE email_log IS
    'Day 25 — per-send record for scheduled email (DESIGN.md §6.4, §8.14). '
    'Service role only (no RLS policies). The partial unique index '
    'email_log_weekly_dedup is the cron idempotency primitive.';
