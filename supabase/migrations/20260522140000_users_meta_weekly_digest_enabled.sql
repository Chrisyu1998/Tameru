-- users_meta.weekly_digest_enabled — Day 25 (DESIGN.md §6.4, §8.7).
--
-- The single authoritative gate for the weekly digest. Three paths flip
-- this boolean and the cron's eligibility predicate reads it:
--   1. Settings → Notifications toggle (user JWT, owner-UPDATE RLS via the
--      existing users_meta_owner policy — no policy change needed).
--   2. One-click List-Unsubscribe handler at GET/POST /unsubscribe
--      (service role; the request carries no JWT by RFC 8058 design).
--   3. Resend bounce/complaint webhook at POST /webhooks/resend (service
--      role; the request is from Resend, not a logged-in user).
--
-- Default true: a fresh user is opted in. Honoring opt-out at any of the
-- three paths is uniform — every flip is a single column UPDATE.

ALTER TABLE users_meta
    ADD COLUMN weekly_digest_enabled boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN users_meta.weekly_digest_enabled IS
    'Day 25 — weekly digest opt-out gate. Flipped by Settings toggle '
    '(user JWT), /unsubscribe (service role, HMAC-token-verified), or '
    'Resend bounce/complaint webhook (service role). DESIGN.md §6.4.';
