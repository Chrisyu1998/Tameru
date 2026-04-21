# Day 25 — Weekly email digest (Resend + Claude Sonnet narrative + pg_cron)

## Goal

Every Monday morning, every active user gets an email: total spend last week vs. weekly average, top category, one AI observation, one nudge if applicable. Five lines max.

## Read first

- `DESIGN.md` §6.4 (digest content + brevity rules), §11 (cost: ~$0.07/user/month for Sonnet).

## Deliverables

- Backend:
  - `app/integrations/resend.py` — thin wrapper around the Resend SDK. Methods: `send_email(to, subject, html, text)`. Logs send attempts (not body) to a `email_log` table with `user_id, sent_at, success, error_code`.
  - New migration: `email_log(id, user_id, kind, sent_at, success, error_code)` with RLS.
  - `app/digest.py`:
    - `compose_digest(user_jwt) -> DigestPayload`:
      - Pulls last week's transactions (Mon–Sun previous week).
      - Computes total spend, vs. user's trailing weekly average.
      - Identifies top category and whether above/below baseline.
      - Calls Claude Sonnet with a tight system prompt: "Given this week's spending data, return a JSON with `observation` (one sentence) and `nudge` (one sentence or null). Both must be ≤ 100 characters. Tone: matter-of-fact, not chirpy."
      - Returns a `DigestPayload` with all fields.
    - `render_email(payload) -> {subject, html, text}` — both HTML and plaintext versions, ≤ 5 lines of body each.
  - `app/cron/digest.py`:
    - Function `send_weekly_digests()` iterates all active users (not opted out, not currently displaced). For each, composes and sends. Failures logged to Sentry, don't halt the batch.
  - This one runs on Railway as a separate scheduled service (`pg_cron` can't make HTTP calls to Resend), or as a daily Railway cron job invoking a CLI entrypoint. Pick the simpler option: Railway cron service running `python -m app.cron.digest` every Monday at 09:00 in the user's local timezone (use UTC for v1; per-user timezone is Phase 2).
- Frontend:
  - Settings → Notifications: toggle for "Weekly digest email" (default on).
  - `users_meta` gets a new column `weekly_digest_enabled boolean DEFAULT true` via migration.

## Don't

- Don't write the digest in Markdown. Render to HTML directly with inline styles for email-client compatibility.
- Don't send to users who haven't logged any transactions in the past 4 weeks (avoids zombie sends). Include this as a `WHERE` clause in the user iteration.
- Don't pad the email past 5 lines. Brevity is the feature.

## Done when

- Manually running `python -m app.cron.digest` against a test user produces a real email in under 5 lines.
- The Sonnet narrative reads as factual, not chirpy.
- Opt-out toggle stops the email immediately.
- `email_log` rows are written for every send attempt.
