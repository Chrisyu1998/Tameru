# Day 28 — Playwright E2E + Phase 1 launch (invite-only)

## Goal

A Playwright suite covers the golden path. PWA Lighthouse score holds ≥ 90. Production deploy is healthy. First 3 invited users can sign up.

## Read first

- `DESIGN.md` §13.5 (E2E spec), §15 Phase 1 deliverables.

## Deliverables

- `frontend/e2e/` (Playwright):
  - Test 1 — **Sign in golden path (new user):** philosophy → take tour → tour completes → sign in with Google → confirm home currency (UX frame 3a) → add first card (frame 4) → skip CSV → land on home.
  - Test 2 — **Log a transaction via chat:** tap chat → type "spent $47 at Trader Joe's on my Amex Gold" → parse card (frame 15) renders with five fields → tap "looks right" → row appears in the Breakdown list → entry-moment insight bubble renders inline in chat.
  - Test 3 — **Edit a transaction via the list:** tap Breakdown → tap "Dining" → tap "see all dining" → tap a row → edit sheet opens → change amount → Save → list reflects the change.
  - Test 4 — **CSV import:** upload a fixture CSV → preview → confirm → progress events stream → final count matches.
  - Test 5 — **Ask AI chat one question:** type "how much did I spend on groceries last week?" → see streaming → see answer with the right number → assert no console errors.
  - Test 6 — **Add subscription via chat, run cron, see auto-logged transaction:** in chat, say "add my Netflix subscription, $15/month, starts today" → confirm parse card → manually invoke `autolog_subscriptions()` via a test endpoint → assert new transaction with 🔄 badge.
  - Test 7 — **Sign out:** click sign out → land on `/signin` → reload → still signed out.
- `.github/workflows/e2e.yml`:
  - On push to `main`, after deploy to Railway, runs Playwright against the deployed URL.
  - Uses dedicated test users in the prod Supabase project (or a separate staging project — your call).
- **Lighthouse check in CI:** PWA score ≥ 90, performance ≥ 80 on the home page.
- **Pre-launch checklist** (`docs/launch_checklist.md`):
  - All migrations applied to prod.
  - All env vars set in Railway.
  - `pg_cron` jobs scheduled and verified (subscription auto-logger, AICallLog aggregator, memory prune).
  - Resend domain verified, weekly digest test send works.
  - Anthropic ZDR request filed.
  - Sentry receiving events.
  - PostHog receiving events.
  - Eval harness latest run: categorization ≥ 88%, chat extraction amount+merchant ≥ 93%, multi-hop tool sequence ≥ 85%.
  - Domain bound, HTTPS valid.
  - `.env.example` matches actual required env vars.
  - Privacy disclosure copy matches reality.
- **Invite first 3 friends:**
  - Add their emails to a Supabase auth allowlist (or just share the URL — Google OAuth lets them sign up).
  - Send a 3-line message: what it is, what's missing, what feedback you want.

## Don't

- Don't launch publicly. Tameru is committed invite-only per `DESIGN.md` §3.3 — there is no public launch on the roadmap.
- Don't skip Playwright for "I tested it manually" — the suite is what catches regressions on Days 29+.
- Don't promise a Pro/paid tier ever. Tameru has no paid tier (§3.3) — no Stripe, no pricing copy, no premium-feature gating.

## Done when

- All 6 Playwright tests pass against production.
- Lighthouse PWA ≥ 90, performance ≥ 80.
- 3 invited friends have working accounts and have logged at least one transaction each.
- The launch checklist is fully checked.
- You've written down the next 3 things to fix based on initial use.

---

**End of the planned 28-day build.** Anything further is optional and author-driven — see `DESIGN.md` §15 "Post-Phase 1 — optional, author-driven only" for the menu (card recommender, recurring detection, receipt photo, etc.). No commitments; build only what you actually want.

A natural next step worth flagging: the **Flash-Lite chat agent A/B test** (DESIGN.md §16). Day 22's eval harness supports `--model gemini-3.1-flash-lite-preview`. Run the multi-hop suite against both Haiku and Flash-Lite once you have ≥ 30 days of real chat data. If Flash-Lite holds within 10% of Haiku's accuracy, swap and save ~$18/month.
