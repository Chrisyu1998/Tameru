# Day 28 — Playwright E2E + Phase 1 launch (invite-only)

## Goal

A Playwright suite covers the five `DESIGN.md` §13.5 golden-path flows against the deployed prod URL, gated by an existing-pattern `scripts/smoke_prod.py` pre-check. PWA Lighthouse score holds ≥ 90 (gate). Production deploy is healthy. First 3 invited users can sign up.

## Read first

- `DESIGN.md` §13.5 (E2E spec — five flows; this prompt matches the spec one-for-one), §15 Phase 1 deliverables, §3.3 invite-only scope.
- `scripts/smoke_prod.py` — the existing prod-smoke pattern. The E2E suite extends this pattern (dedicated test user, password auth, no service role in the request path), not invent a new one.

## Deliverables

### `frontend/e2e/` (Playwright, five tests matching §13.5)

The five tests sign in as an **ephemeral per-run E2E user** (see `scripts/e2e_user.py` below) — not the persistent `smoke_prod.py` user — so the full onboarding wizard runs every time. `home_currency` is set once during onboarding and never reset (invariant 13 immutability trigger), so reuse-then-reset isn't an option; create-fresh-delete-after is.

- **Test 1 — Sign-up golden path (fresh user):** philosophy → take tour → tour completes → sign in via `auth.signInWithPassword` (Playwright `page.evaluate` against the in-page `supabase-js` client, per the memory.md 2026-05-20 "Browser-driving" learning) → confirm home currency (UX frame 3a) → add first card (frame 4) → skip CSV → land on home. No Google OAuth, no magic-link.
- **Test 2 — Log a transaction via chat:** tap chat → type "spent $47 at Trader Joe's on my Amex Gold" → parse card (frame 15) renders with five fields → tap "looks right" → row appears in the Breakdown list → entry-moment insight bubble renders inline in chat.
- **Test 3 — CSV import:** upload a fixture CSV (reuse a small file from `tests/eval/fixtures/` so the suite stays in sync with eval inputs) → preview → confirm → progress events stream → final count matches.
- **Test 4 — Ask AI chat one question:** type "how much did I spend on groceries last week?" → see streaming → see an answer that includes a dollar figure → assert no console errors and no Sentry-suppressed exception bubbled to the UI.
- **Test 5 — Sign out:** click sign out → land on `/signin` → reload → still signed out.

Tests **not** in this suite (and where they're covered instead):

- **Edit-a-transaction** — covered by Vitest unit tests on `EditTransactionSheet` + the existing backend integration test on `PATCH /transactions/{id}`. Not §13.5.
- **Subscription auto-log + 🔄 badge render** — auto-logger correctness is pinned by Day 19's integration test (UNIQUE constraint + advisory lock + idempotency). The UI badge is a Vitest snapshot if regression-pinning is wanted. Not §13.5, and an E2E path would require a `next_billing_date`-mutating backdoor — invariant 8 surface area Day 28 should not introduce.

### `scripts/e2e_user.py`

Sanctioned service-role caller under `scripts/` (already excluded from `tests/contracts/test_no_service_role_leak.py`). Two subcommands:

- `python scripts/e2e_user.py create` → uses `auth.admin.createUser` to mint `e2e+<unix_ts>@tameru.xyz` with a random password and `email_confirm=True`. Prints two lines to stdout: `E2E_TEST_EMAIL=...` and `E2E_TEST_PASSWORD=...`. Also writes the new auth user_id to a third line so teardown can target it precisely. The CI step captures these into `$GITHUB_ENV`.
- `python scripts/e2e_user.py delete --user-id=<uid>` → `auth.admin.deleteUser(uid)`. The FK `ON DELETE CASCADE` from every user-content table to `auth.users` cleans up rows automatically; no per-table deletes required.

Sets `users_meta.analytics_opted_out=true` and `users_meta.weekly_digest_enabled=false` on creation so the E2E user produces zero PostHog requests and the weekly digest cron never picks them up.

### `.github/workflows/e2e.yml` (or new job in `ci.yml`)

Pipeline shape:

1. Runs on `push: branches: [main]`, **not** on PRs (solo-dev velocity; PR-time E2E is wasted overhead).
2. `needs: [deploy-frontend, migrate-prod]` — Railway has "Wait for CI" on already (memory.md 2026-05-22), so the Railway image lands after these.
3. Poll the Railway healthcheck (`curl -fsS $BACKEND_PUBLIC_URL/healthz` until 200 without the `x-railway-fallback: true` header, ≤ 5 min, fail loud on timeout).
4. `python scripts/smoke_prod.py` — synchronous sanity. If this fails, skip Playwright and fail the job.
5. `python scripts/e2e_user.py create` → `$GITHUB_ENV`.
6. `npx playwright test` against `$FRONTEND_ORIGIN` (currently `https://tameru-seven.vercel.app`).
7. **Always run** (`if: always()`): `python scripts/e2e_user.py delete --user-id=$E2E_USER_ID` so a test failure can't leak the user.

### Lighthouse check in CI

PWA score **≥ 90** is a hard gate (matches `DESIGN.md` §10.1). Performance score **≥ 80** is a recorded **target** — surface the number in the job log but don't fail the build on it. Lighthouse-on-CI variance routinely produces ±5 perf swings on the same code; treating perf as a gate is a flake source. Use `treosh/lighthouse-ci-action` against the home page after the user is signed in (or against the unauthenticated landing — author's call; whichever is more representative).

### Pre-launch checklist (`docs/launch_checklist.md`)

Each item must be checkable from outside (no "looks good" language).

- `main` is green on `migrate-prod` (proves prod schema matches `supabase/migrations/`; memory.md 2026-05-22 — CI is the only sanctioned writer to prod `schema_migrations`).
- Backend lifespan boot check passes on Railway (proves every `_REQUIRED_ENV_VARS` entry is set). Check by hitting `/healthz` and confirming the cold-start log line `application startup complete`.
- `pg_cron` jobs are scheduled and have at least one row in `cron.job_run_details` from the last 7 days for each of: subscription auto-logger, AICallLog aggregator, memory prune, weekly digest cron.
- Resend domain verified (DNS records green in dashboard); a manual `python scripts/smoke_digest.py --user-id=<self>` (or equivalent one-shot) ships a real digest to the author's inbox; the CTA opens `${FRONTEND_ORIGIN}/?source=digest` and fires `weekly_digest_opened` (Day 26b).
- Anthropic ZDR request filed; status recorded in `docs/zdr_request.md` per §9.4.
- **Sentry wiring confirmed** by running `python -c "import sentry_sdk; sentry_sdk.init(dsn='$SENTRY_DSN', environment='production'); sentry_sdk.capture_message('launch-day smoke', level='info'); sentry_sdk.flush()"` locally with prod DSN; event appears in Sentry within 30 s. (Don't try to verify Sentry via the E2E suite — the `before_send` filters in §14.5 drop 4xx and `app.integrations.*` / `app.agent.*` exceptions by design, so casual errors won't ship.)
- PostHog receiving events from the whitelist (Day 26): verify `chat_session_started`, `feature_used`, `onboarding_step_completed` (all populated by the E2E run itself) and `weekly_digest_opened` (fire by tapping the digest CTA from a real inbox) appear in the PostHog Live Events view. Confirm the opted-out E2E user produces zero PostHog network requests across a full Playwright run (DevTools network filter on `i.posthog.com`).
- Eval harness latest run meets the gates pinned in Day 22's CI config — reference `tests/eval/` thresholds rather than restating numbers here (avoid drift).
- **MCP server smoke** (Days 23a/23b): add the prod MCP URL as a custom connector in Claude.ai web, complete the OAuth consent dance, ask "list my cards" and confirm tool call + structured response. (Automated MCP coverage is blocked by `supabase-py` OAuth-method lag per memory.md 2026-05-22 — this stays UAT-only at v1.)
- Frontend reachable at a stable HTTPS URL (currently `https://tameru-seven.vercel.app`; custom-domain on the PWA is deferred — `tameru.xyz` is email-only today per memory.md 2026-05-26).
- `.env.example` matches `_REQUIRED_ENV_VARS` exactly — diff them. Includes (non-exhaustive): `VITE_POSTHOG_KEY`, `VITE_POSTHOG_HOST=https://us.i.posthog.com`, `FRONTEND_ORIGIN`, `IMPORT_TOKEN_SECRET`, `MCP_RESOURCE_SERVER_URL`, `DIGEST_UNSUBSCRIBE_SECRET`, `RESEND_WEBHOOK_SECRET`.
- Privacy disclosure copy matches reality — §9.4 (AI providers, ZDR status) **and** §9.5 (PostHog whitelist, US Cloud region) both rendered in Settings and on `/privacy`.

### Rollback runbook (append to `docs/launch_checklist.md`)

> **If a friend reports a broken state they can't recover from:** (1) `git log --oneline -10` to find the suspect commit. (2) `git revert <sha>` + push to main. (3) Wait for CI green + Vercel + Railway redeploys (~10 min). (4) Text the friend when fixed.
>
> **Migrations are not auto-rollback-safe.** v1 migrations are additive (new columns, new tables) so reverting application code while leaving the schema is safe in nearly every case. If a migration is itself the cause, write a *forward* migration that nulls/drops the offender — don't `supabase db push` from local to recover (memory.md 2026-05-22 — CI is the only sanctioned writer to prod `schema_migrations`).

### Invite first 3 friends

- Share `${FRONTEND_ORIGIN}` directly. Google OAuth handles signup with no allowlist or invite-code surface (no such gating exists in v1; do not invent one).
- Send a 3-line message: what it is, what's missing, what feedback you want.

## Don't

- Don't launch publicly. Tameru is committed invite-only per `DESIGN.md` §3.3 — there is no public launch on the roadmap.
- Don't skip Playwright for "I tested it manually" — the suite is what catches regressions on any post-launch fix (the §15 author-driven menu).
- Don't promise a Pro/paid tier ever. Tameru has no paid tier (§3.3) — no Stripe, no pricing copy, no premium-feature gating.
- Don't invent a `next_billing_date`-mutating endpoint or any other backdoor mutation surface to make a flow testable from E2E — the propose-then-confirm doctrine (invariant 8) is the surface that protects the ledger, and a test-only endpoint widens it.
- Don't add the service-role key to the frontend project's env. The E2E user lifecycle lives in `scripts/e2e_user.py` (backend, already sanctioned) — Playwright only ever sees the resulting email + password.

## Done when (engineering)

- All 5 Playwright tests pass against production via the post-deploy CI job.
- Lighthouse PWA ≥ 90 (gated); performance score recorded.
- Pre-launch checklist is fully checked.
- Rollback runbook landed in `docs/launch_checklist.md`.

## Launch successful when

- 3 invited friends have working accounts and have logged at least one transaction each.
- The next 3 things to fix from initial use are written down (issue tracker or `docs/post-launch-followups.md`).

---

**End of the planned 28-day build.** Anything further is optional and author-driven — see `DESIGN.md` §15 "Post-Phase 1 — optional, author-driven only" for the menu (card recommender, recurring detection, receipt photo, etc.). No commitments; build only what you actually want.

A natural next step worth flagging: the **Flash-Lite chat agent A/B test** (DESIGN.md §16). Day 22's eval harness supports `--model gemini-3.1-flash-lite-preview`. Run the multi-hop suite against both Haiku and Flash-Lite once you have ≥ 30 days of real chat data. If Flash-Lite holds within 10% of Haiku's accuracy, swap and save ~$18/month.
