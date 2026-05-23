# Day 25 — Weekly email digest (Resend + Claude Sonnet + List-Unsubscribe + bounce handling)

## Goal

Every Monday at 09:00 ET (14:00 UTC), every eligible user gets a ≤5-block email: total spend last week vs. trailing 8-week average, top category vs. its baseline, one AI-generated observation, one nudge if applicable. The system honors opt-out three ways (Settings toggle, one-click List-Unsubscribe per RFC 8058, Resend hard-bounce/complaint webhooks) and never sends twice in the same week.

## Read first

- `DESIGN.md` §6.4 (digest content + brevity rules + cadence + opt-out), §8.7 (`users_meta` v1 schema — this prompt adds `weekly_digest_enabled`), §8.8 (`ai_call_log`, task_type `digest`), §8.14 (new — `email_log`), §11 (cost: ~$0.07/user/month for Sonnet), §13 (`RESEND_API_KEY` + the two new env vars this prompt adds).
- `CLAUDE.md` invariants 1 (this prompt promotes the digest cron and Resend webhook to explicit sanctioned service-role callers — see the amendment below) and 14 (system-level `ai_call_log` writes under service role; the digest job is no longer "future").
- `memory.md` 2026-05-19 "PostgREST `on_conflict` can't infer partial unique indexes" (the `email_log` weekly-dedup index is partial — same workaround pattern applies), 2026-05-19 "Supabase Python has no multi-statement transaction primitive", 2026-05-19 "Day 20 CSV import: stateless HMAC `import_token`" (unsubscribe-token pattern parallels this).

## Prerequisites (one-time operational setup; not code)

These must be done before the first real send or Gmail will spam-fold the message. They are not deliverables, but the Done-when can't pass without them.

- **Own `tameru.app`.** Per memory.md 2026-05-20, the canonical DESIGN.md hosts are not currently owned. Buy the domain (Namecheap/Cloudflare, ~$15/yr).
- Send from a **subdomain** — `mail.tameru.app` — not the root. Contains any deliverability incident.
- In Resend dashboard: add `mail.tameru.app` as a sending domain; copy the SPF + DKIM + DMARC DNS records they provide and add them at the registrar. Start DMARC with `p=none` (monitor); move to `p=quarantine` after one month of clean reports. Do not ship `p=reject` from day one.
- In Resend dashboard **project settings: disable open tracking and click tracking**. Privacy posture (CLAUDE.md) — open-tracking pixels exfiltrate recipient IP to a third party on every email open; click tracking rewrites every link through `resend.com`.
- Add three env vars to Railway and to `_REQUIRED_ENV_VARS` in `app/main.py`: `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET` (Svix signing secret from Resend dashboard), `DIGEST_UNSUBSCRIBE_SECRET` (32 random bytes, base64; same shape as `IMPORT_TOKEN_SECRET`).

## Deliverables

### 1. Schema (two migrations)

**Migration A — `..._users_meta_weekly_digest_enabled.sql`:**

```sql
ALTER TABLE users_meta
  ADD COLUMN weekly_digest_enabled boolean NOT NULL DEFAULT true;
```

Existing owner-SELECT and owner-UPDATE RLS policies on `users_meta` cover the new column automatically.

**Migration B — `..._email_log.sql`:**

```sql
CREATE TABLE email_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('digest')),
  sent_at timestamptz NOT NULL DEFAULT now(),
  success boolean NOT NULL,
  provider_message_id text,
  error_code text,
  bounce_type text CHECK (bounce_type IN ('hard', 'soft', 'complaint') OR bounce_type IS NULL)
);

-- Weekly idempotency: only successful sends count. Re-sending after a transient failure is allowed.
CREATE UNIQUE INDEX email_log_weekly_dedup
  ON email_log (user_id, kind, date_trunc('week', sent_at))
  WHERE success;

-- Webhook lookup by provider message id.
CREATE INDEX email_log_provider_message_id
  ON email_log (provider_message_id)
  WHERE provider_message_id IS NOT NULL;

ALTER TABLE email_log ENABLE ROW LEVEL SECURITY;
-- No policies: service role only (no user-facing reads in v1). Same posture as stripe_events (DESIGN.md §8.10).
```

### 2. Service-role posture (CLAUDE.md invariant 1 + 14 amendment)

The digest cron has no user JWT in scope and the Resend webhook has no user JWT in scope. Both must use the service role. This prompt promotes both from invariant 1's implicit/future categories to explicit current-v1 sanctioned callers. Update CLAUDE.md invariants 1 and 14 in the same change. Update `tests/contracts/test_no_service_role_leak.py` to add a per-file allowlist for `app/routes/webhooks_resend.py` with a rationale comment (the webhook can't carry a user JWT — the request is from Resend, not a logged-in user). Files under `app/cron/` remain excluded by the directory rule.

Code organization:

- `app/services/digest.py` — `compose_digest(client, user_id, user_email) -> DigestPayload`, `render_email(payload, unsubscribe_url) -> RenderedEmail`. Accepts a Supabase client as a parameter; does **not** import `supabase_admin`. Service-role-leak test stays green for this file.
- `app/cron/digest.py` — entry point. Constructs the admin client. Iterates eligible users. May import `supabase_admin` (file is under `app/cron/`, excluded by the leak test).
- `app/routes/webhooks_resend.py` — webhook handler. Imports `supabase_admin`. Listed in the test's per-file allowlist with rationale.

### 3. Resend integration

- `pyproject.toml`: `resend>=2.30,<3.0`, `svix>=1.40` (webhook signature verification).
- `app/integrations/resend.py` — `send_digest_email(to, subject, html, text, *, list_unsubscribe_url, list_unsubscribe_mailto) -> SendResult`:
  - Wraps `resend.Emails.send`. Sets `headers={"List-Unsubscribe": "<url>, <mailto:...>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}`.
  - Always passes both `html` and `text`. Plaintext is required for deliverability — spam filters score HTML/text similarity.
  - From: `"Tameru" <hello@mail.tameru.app>`. Reply-To: `hello@mail.tameru.app` (aliased to a real inbox; users hitting reply must reach a human).
  - Returns `{message_id: str | None, success: bool, error_code: str | None}`. Does **not** log to `email_log` itself — the caller does, after combining with cron context. This wrapper stays a pure boundary adapter.

### 4. Compose + render

`app/services/digest.py`:

- `compose_digest(client, user_id, user_email)`:
  - Pulls last week's `active` transactions for `user_id` (Mon 00:00 ET → Sun 23:59:59 ET previous week). Use `pytz.timezone("America/New_York")` to compute the boundaries; convert to UTC for the query.
  - Computes: total spend; trailing 8-week average (excluding the just-ended week); top category by sum; whether top category is above/below its own 8-week baseline.
  - Calls Claude Sonnet with a tight system prompt requesting JSON `{"observation": "...", "nudge": null | "..."}` — both ≤100 chars; matter-of-fact; no exclamation marks; no second-person imperatives.
  - **Sends only aggregates to Sonnet** — category totals and week-over-week deltas. No merchant names, no raw transaction rows. (Privacy posture: Anthropic has ZDR but minimize surface anyway.)
  - Logs the Sonnet call to `ai_call_log` via service role with `task_type='digest'`, `provider='anthropic'`, `model='claude-sonnet-4-6'`, all 10 fields populated (CLAUDE.md invariant 14). `user_id` is set to the digest recipient so per-user cost math works.
- `render_email(payload, unsubscribe_url)`:
  - Both HTML and plaintext versions; ≤5 distinct content blocks each (one block = one sentence or one data line).
  - HTML uses inline styles only — Gmail strips `<style>` blocks and Tailwind class names.
  - Both versions include a **visible** "Unsubscribe" link pointing at `unsubscribe_url` (defense in depth alongside the header).

### 5. Cron entry point

- `app/cron/digest.py`:
  - `send_weekly_digests() -> SendReport` — invoked by Railway scheduled service with cron `0 14 * * 1` (Monday 14:00 UTC = 09:00 ET). Per-user timezone is Phase 2; v1 hardcodes ET.
  - Single eligibility query (deliverable 6 below) joining `auth.users` + `users_meta` + a `transactions` existence-in-past-4-weeks predicate. Don't loop and filter in Python — system-level callers must filter activity in SQL.
  - For each row: mint per-user one-click unsubscribe URL (HMAC, deliverable 7); call `compose_digest`; call `render_email`; call `send_digest_email`; insert into `email_log` with the returned `provider_message_id`.
  - Idempotency: the `email_log` insert is `INSERT … ON CONFLICT DO NOTHING` against `email_log_weekly_dedup`. Per memory.md 2026-05-19 "PostgREST `on_conflict` can't infer partial unique indexes," this insert goes through a SECURITY INVOKER plpgsql RPC (`email_log_insert_idempotent`) — the function emits the matching `WHERE success` predicate so Postgres can use the partial index. Pre-check `SELECT 1 FROM email_log WHERE …` before composing the digest, so we don't burn a Sonnet call on a re-run.
  - Per-user errors don't halt the batch: catch, log via the JSON logger (correlation-id wired by Day 24), insert an `email_log` row with `success=false`, continue.
  - **Sentry filter update:** add `"app.services.digest"` and `"app.integrations.resend"` to `_AI_INTEGRATION_MODULES` in `app/sentry_filters.py`. Sonnet/Resend 5xx are already in `ai_call_log` / `email_log`; double-logging to Sentry contradicts Day 24's contract. `AICallLogError` canary path still fires.
  - CLI: `python -m app.cron.digest [--user <user_id>] [--dry-run]`. `--user` restricts to a single user (Done-when relies on this). `--dry-run` composes and renders but skips Resend send + `email_log` write — prints the rendered HTML and plaintext to stdout.

### 6. Eligibility predicate (one SQL query)

```sql
SELECT u.id, u.email
FROM auth.users u
JOIN public.users_meta m ON m.user_id = u.id
WHERE u.email IS NOT NULL
  AND u.email_confirmed_at IS NOT NULL
  AND u.deleted_at IS NULL
  AND m.weekly_digest_enabled = true
  AND EXISTS (
    SELECT 1 FROM public.transactions t
    WHERE t.user_id = u.id
      AND t.created_at > now() - interval '4 weeks'
      AND t.status = 'active'
  );
```

### 7. One-click unsubscribe (RFC 8058)

- `app/util/unsubscribe.py`:
  - `make_unsubscribe_token(user_id, kind) -> str` — HMAC-SHA256 of `f"{user_id}|{kind}"` keyed with `DIGEST_UNSUBSCRIBE_SECRET`, base64url-encoded. No expiry — a year-old unsubscribe link should still work.
  - `verify_unsubscribe_token(token, user_id, kind) -> bool` — `hmac.compare_digest` (constant-time).
- `app/routes/unsubscribe.py`:
  - `GET /unsubscribe?user=<uuid>&kind=digest&token=<hmac>` — verifies; on success flips `weekly_digest_enabled=false` via service role; renders a minimal static HTML "You're unsubscribed from weekly digest emails. Update preferences in the app." page.
  - `POST /unsubscribe?user=<uuid>&kind=digest&token=<hmac>` — same effect, required by RFC 8058 (Gmail/Yahoo bulk-sender spec). Returns 200 with empty body.
  - Both routes are service-role only and bypass the JWT auth dependency — explicitly listed in the auth bypass allowlist. Also exempted from the service-role leak test (per-file allowlist + rationale).
- `List-Unsubscribe` header value (set by the Resend wrapper from the URL + mailto args):
  ```
  List-Unsubscribe: <https://tameru.app/unsubscribe?user=…&kind=digest&token=…>, <mailto:unsubscribe@mail.tameru.app?subject=user=…+kind=digest+token=…>
  List-Unsubscribe-Post: List-Unsubscribe=One-Click
  ```
- Gmail's >5K/day threshold for required one-click unsubscribe doesn't apply at v1 scale (~10 users), but ship it anyway: inbox placement benefits, and retrofit-after-reputation-damage is much harder than ship-it-now.

### 8. Resend webhook

- `app/routes/webhooks_resend.py`:
  - `POST /webhooks/resend` — verifies Svix signature against `RESEND_WEBHOOK_SECRET` using the `svix` library. Always returns 200 (non-2xx triggers Resend retry, which only adds noise).
  - Bypasses JWT auth dependency (no JWT — request is from Resend).
  - Handles three events:
    - `email.bounced` with `data.bounce.type == "hard"`: look up `email_log` row by `provider_message_id`, set `bounce_type='hard'`, set `users_meta.weekly_digest_enabled=false` for the affected user.
    - `email.complained`: same as hard bounce (set `bounce_type='complaint'`, disable digest).
    - `email.delivery_delayed`: log to stdout only; do not suppress.
  - Soft bounces are not surfaced — Resend retries internally.
  - Unknown `provider_message_id` is a 200 no-op (could be a webhook arriving after `email_log` cleanup, or a webhook for a non-digest send when welcome sequence ships later).
  - Listed in `tests/contracts/test_no_service_role_leak.py`'s per-file allowlist with rationale comment.

### 9. Frontend Settings toggle

- Settings → Notifications: one toggle "Weekly digest email", default on. PATCH `/me/preferences` (add the endpoint under user JWT — owner-UPDATE RLS covers the column). Optimistic UI; reads `users_meta.weekly_digest_enabled` on mount.
- Copy under the toggle: "Sent Monday mornings. You can also unsubscribe from any email's footer."

### 10. DESIGN.md + CLAUDE.md sync (in the same commit as the code)

- DESIGN.md §6.4 — expand to include cadence "Monday 09:00 ET (14:00 UTC); per-user timezone is Phase 2", sender identity, deliverability prerequisites, opt-out (toggle + one-click + bounce-driven), idempotency rule, cost.
- DESIGN.md §8.7 — add `weekly_digest_enabled boolean NOT NULL DEFAULT true` to the v1 schema table.
- DESIGN.md §8.14 (new) — `email_log` table spec (mirrors §8.8 shape; service-role only; the weekly-dedup partial unique index is the load-bearing idempotency primitive).
- DESIGN.md §13 — add `RESEND_WEBHOOK_SECRET` and `DIGEST_UNSUBSCRIBE_SECRET` to the env-var list.
- CLAUDE.md invariant 1 — list the digest cron and Resend webhook as the third and fourth sanctioned service-role callers (alongside `pg_cron` and CLI migrations).
- CLAUDE.md invariant 14 — promote "future digest jobs" to "the digest cron job (`app/cron/digest.py`)".

### 11. Tests

- `tests/test_unsubscribe_token.py` — round-trip; tampered token rejected; constant-time compare honored (no early return on prefix match).
- `tests/test_unsubscribe_route.py` — valid token flips `weekly_digest_enabled` and renders the success page; bad token returns 403 without state change; POST (one-click) has the same effect as GET; works without a session JWT.
- `tests/test_resend_webhook.py` — Svix signature verification; hard-bounce suppresses + sets `bounce_type='hard'` on the matching `email_log` row; complaint suppresses; soft-bounce does not suppress; unknown `provider_message_id` returns 200 no-op; missing/invalid signature returns 400.
- `tests/test_compose_digest.py` — seed deterministic weekly spend; mocked Sonnet returns canned JSON; assert payload aggregates math correct; assert `ai_call_log` row written with all 10 fields and correct `user_id`; assert no merchant names or transaction rows in the payload sent to Sonnet (privacy boundary regression guard).
- `tests/test_digest_cron.py` — seed: eligible user, opted-out user, zombie user (no tx in 4w), recently-sent user (already has a `success=true` row this week), unconfirmed-email user, soft-deleted user. Run `send_weekly_digests()`. Assert only the eligible user receives a send. Re-run is a zero-send no-op. Per-user Resend 500 mid-batch does not halt subsequent users.
- `tests/contracts/test_no_service_role_leak.py` — verify it still passes (`app/services/digest.py` does not import `supabase_admin`; `app/routes/webhooks_resend.py` is in the per-file allowlist with rationale).

## Don't

- Don't send from `onboarding@resend.dev` or any domain you don't own — Gmail will spam-fold.
- Don't enable Resend open or click tracking (privacy posture; open pixel exfiltrates recipient IP).
- Don't write the digest in Markdown — render HTML directly with inline styles.
- Don't pad past 5 content blocks. Brevity is the feature.
- Don't pass `user_jwt` to anything in this path — the digest cron is service-role only by design (invariant 1 amendment).
- Don't double-log Sonnet/Resend 5xx to Sentry — add `app.services.digest` and `app.integrations.resend` to `_AI_INTEGRATION_MODULES`.
- Don't send merchant names or raw transaction rows to Sonnet — aggregates only.
- Don't skip the `email_log` write on success — it's the idempotency primitive that prevents re-sends within the same week.
- Don't honor a Resend webhook on a soft bounce — Resend retries internally; suppressing the user on a transient blip is wrong.
- Don't skip the visible unsubscribe link in the body. Header alone is insufficient — a visible link is what users actually find.
- Don't ship without the SPF/DKIM/DMARC records in DNS. Prerequisite, not a deliverable, but Done-when fails without it.

## Done when

- `python -m app.cron.digest --user <test_user_id> --dry-run` prints a rendered HTML body of ≤5 content blocks and a plaintext body of ≤5 content blocks.
- `python -m app.cron.digest --user <test_user_id>` (no dry-run) lands a real email in the test user's inbox.
- Gmail's "Show original" on a received digest shows: SPF=PASS, DKIM=PASS, DMARC=PASS.
- The received email has the `List-Unsubscribe` header with both URL + mailto, and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, AND a visible Unsubscribe link in the body.
- Tapping the unsubscribe link in the body flips `users_meta.weekly_digest_enabled` to false. Re-running the cron skips that user.
- Posting a Svix-signed `email.bounced` (hard) webhook for a sent message id flips `weekly_digest_enabled=false` for the affected user, sets `bounce_type='hard'` on the matching `email_log` row, and the next cron skips them.
- Posting a Svix-signed `email.bounced` (soft) for the same does NOT suppress.
- Re-running `python -m app.cron.digest` on the same Monday produces zero new sends; `email_log.success=true` row count unchanged.
- One `ai_call_log` row with `task_type='digest'`, `model='claude-sonnet-4-6'`, all 10 fields populated, and correct `user_id` is written per send.
- A Resend SDK 500 mid-batch for one user does not halt the batch; subsequent users are processed; the failing user gets an `email_log` row with `success=false`.
- `tests/contracts/test_no_service_role_leak.py` passes.
- DESIGN.md §6.4 / §8.7 / §8.14 / §13 and CLAUDE.md invariants 1 + 14 are updated in the same commit as the code.
