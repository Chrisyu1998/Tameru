# Tameru — Phase 1 launch checklist

Day 28. Invite-only v1 to ~10 friends and family per `DESIGN.md` §3.3.
Each item must be checkable from outside (no "looks good" language).

## Pre-launch checklist

### Schema + deploy

- [ ] `main` is green on the `migrate-prod` job (CI is the only sanctioned
      writer to prod `schema_migrations`; memory.md 2026-05-22).
- [ ] Railway web service: lifespan boot check passes (every entry in
      `_REQUIRED_ENV_VARS` is set). Check by hitting
      `$BACKEND_PUBLIC_URL/healthz` and tailing the cold-start log line
      `application startup complete`.
- [ ] Railway `digest-cron` service: env vars set
      (`RESEND_API_KEY`, `ANTHROPIC_API_KEY`, `DIGEST_UNSUBSCRIBE_SECRET`,
      shared `SUPABASE_*` + `BACKEND_PUBLIC_URL`); cron schedule `0 14 * * 1`;
      Wait-for-CI ON.
- [ ] `frontend/.env.example` matches `_REQUIRED_ENV_VARS` exactly — diff
      them. Includes `VITE_POSTHOG_KEY`, `VITE_POSTHOG_HOST=https://us.i.posthog.com`,
      `FRONTEND_ORIGIN`, `IMPORT_TOKEN_SECRET`, `MCP_RESOURCE_SERVER_URL`,
      `DIGEST_UNSUBSCRIBE_SECRET`, `RESEND_WEBHOOK_SECRET`.

### Cron + observability

- [ ] `pg_cron` jobs each have at least one row in `cron.job_run_details`
      from the last 7 days:
  - [ ] subscription auto-logger (`autolog_subscriptions`)
  - [ ] AICallLog aggregator (`aggregate_aicalllog`)
  - [ ] memory prune (`prune_user_memory`)
- [ ] Sentry wiring confirmed by running locally with prod DSN:
      ```bash
      python -c "import sentry_sdk; sentry_sdk.init(dsn='$SENTRY_DSN', environment='production'); sentry_sdk.capture_message('launch-day smoke', level='info'); sentry_sdk.flush()"
      ```
      Event appears in Sentry dashboard within 30 s. (Don't try to verify
      Sentry via the E2E suite — the `before_send` filters drop 4xx and
      `app.integrations.*` / `app.agent.*` exceptions by design, so casual
      errors won't ship.)
- [ ] PostHog Live Events shows traffic on the whitelist after the E2E
      run completes: `chat_session_started`, `feature_used`,
      `onboarding_step_completed`, `weekly_digest_opened` (fire by tapping
      the digest CTA from a real inbox — needs the weekly cron to have
      run at least once).
- [ ] E2E user PostHog events are filterable from dashboards by email
      (`e2e+*@tameru.xyz`). The ephemeral user is opted *in* during its
      ~5-minute lifetime (the bootstrap call in CurrencyStep creates
      `users_meta` with the default `analytics_opted_out=false`; we
      can't pre-seed an opt-out row because `home_currency NOT NULL
      DEFAULT 'USD'` would mark the user as already-onboarded). Net
      noise: ~10–20 events per CI run, attributable, then deleted.

### Privacy + email

- [ ] Anthropic ZDR request filed; status recorded in `docs/zdr_request.md`
      (§9.4).
- [ ] Resend domain (`mail.tameru.xyz`) verified — SPF + DKIM + DMARC green
      in the Resend dashboard.
- [ ] Open + click tracking disabled in Resend project settings.
- [ ] Manual `digest-cron` "Run Now" sends a real digest to the author's
      inbox; CTA opens `${FRONTEND_ORIGIN}/?source=digest` and fires
      `weekly_digest_opened` (verifiable in PostHog Live Events).
- [ ] Privacy disclosure copy renders both §9.4 (AI providers, ZDR status)
      AND §9.5 (PostHog whitelist, US Cloud region) on `/privacy` (mobile)
      and `Settings → Privacy` (desktop).

### Eval + integration

- [ ] Eval harness latest run on `main` meets the gates pinned in
      `tests/eval/` thresholds. (Don't restate the numbers here — they
      drift; the gate config in CI is authoritative.)
- [ ] MCP server smoke: add the prod MCP URL as a custom connector in
      Claude.ai web → complete the OAuth consent dance → "list my cards"
      returns the user's wallet. UAT-only because `supabase-py` lags on
      `auth.oauth.*` methods (memory.md 2026-05-22).
- [ ] All 5 deployed Playwright tests pass on the latest `main` deploy
      (`e2e-deployed` CI job green).
- [ ] Lighthouse CI gates pass on the latest deploy (`lighthouse` job
      green). Modern Lighthouse (v12+) retired the entire PWA
      category + its individual audits (installable-manifest,
      service-worker, etc.) — there is no audit to gate on anymore.
      The remaining gates are three category scores (accessibility /
      best-practices / SEO ≥ 0.9) + the `viewport` audit. PWA
      installability + service-worker registration are now build-time
      properties of `vite-plugin-pwa`; a misconfiguration would
      surface as a broken install prompt on a real device rather
      than as a Lighthouse failure. Performance is "off" — the chat
      surface's open SSE connection prevents Lighthouse from settling,
      and CI runner variance makes a hard perf gate flake without
      value.
- [ ] Manually verify PWA install prompt on a real iOS device once
      pre-launch (open https://tameru-seven.vercel.app in Safari →
      Share → Add to Home Screen). The Lighthouse gates no longer
      cover this.

### Frontend reachability

- [ ] Frontend reachable at a stable HTTPS URL — currently
      `https://tameru-seven.vercel.app`. Custom domain on the PWA is
      deferred (`tameru.xyz` is wired for email only — memory.md
      2026-05-26).

## Launch

- [ ] Share `${FRONTEND_ORIGIN}` directly with the 3 invited friends.
      Google OAuth handles signup with no allowlist or invite-code
      surface — do not invent one. A 3-line message: what it is, what's
      missing, what feedback you want.

## Launch successful when

- [ ] 3 invited friends have working accounts.
- [ ] Each of the 3 has logged at least one transaction.
- [ ] The next 3 things to fix from initial use are written down (issue
      tracker or `docs/post-launch-followups.md`).

---

## Rollback runbook

**If a friend reports a broken state they can't recover from:**

1. `git log --oneline -10` to find the suspect commit.
2. `git revert <sha>` + push to `main`.
3. Wait for CI green + Vercel + Railway redeploys (~10 min). The
   `e2e-deployed` job will catch a regression that survives the revert.
4. Text the friend when fixed.

**Migrations are not auto-rollback-safe.** v1 migrations are additive
(new columns, new tables) so reverting application code while leaving
the schema is safe in nearly every case. If a migration is itself the
cause, write a *forward* migration that nulls/drops the offender — do
not `supabase db push` from local to recover (memory.md 2026-05-22 — CI
is the only sanctioned writer to prod `schema_migrations`).

**Do not** revert by pushing directly to `main` with `--force`, by
running migrations from a laptop, or by editing the schema in the
Supabase dashboard SQL editor. All three break the CI-only-writer
invariant and silently desync `schema_migrations` for the next deploy.

If the backend is wholly down (Railway outage, not a code regression),
the symptom on iOS is "Load failed" with a working app shell — diagnosis
ladder is in memory.md 2026-05-20 ("iOS PWA Load failed").
