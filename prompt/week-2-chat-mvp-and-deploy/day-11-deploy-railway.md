# Day 11 — Deploy backend to Railway + frontend to Vercel + smoke test in production

## Goal

The Week 1 backend is live on Railway with all secrets in env vars, the frontend PWA from Day 6 is live on Vercel pointing at the Railway API, CORS is verified cross-origin, CI runs RLS contract tests on every PR, and a smoke test exercises the prod deployment end-to-end.

## Read first

- `DESIGN.md` §5.3 (hosting split: Railway backend + Vercel frontend), §7.5 (Railway grace period — set today), §9.3 (CORS, Bearer-token auth), §13 (testing strategy), §14 (observability + ops).

## Deliverables

### Backend (Railway)

- Railway project created. The repo's `main` branch deploys on push.
- All env vars set in Railway dashboard:
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
  - `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.1-flash-lite-preview`
  - `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-haiku-4-5` (placeholder; used Week 3)
  - `PERPLEXITY_API_KEY` (placeholder; used Day 14)
  - `SENTRY_DSN` (placeholder; wired Day 24)
  - `FRONTEND_ORIGIN=https://tameru.app` — consumed by Day 6's `CORSMiddleware` to allowlist the Vercel origin in production.
- `terminationGracePeriodSeconds: 60` configured in `railway.json` (or service settings).
- Custom subdomain `api.tameru.app` bound to the Railway service (if `tameru.app` is registered; otherwise the Railway-generated URL, and update `FRONTEND_ORIGIN` / Vercel's `VITE_API_URL` to match).

### Frontend (Vercel)

- Vercel project created, linked to the GitHub repo with `frontend/` as the project root. `main` branch deploys on push; PRs get preview URLs.
- Vercel env vars set (production scope):
  - `VITE_API_URL=https://api.tameru.app`
  - `VITE_SUPABASE_URL`
  - `VITE_SUPABASE_ANON_KEY`
- Custom domain `tameru.app` bound to Vercel (if registered).
- `frontend/vercel.json` (from Day 6) is what makes client-side routing resolve on direct URL visits — confirm that a direct visit to `https://tameru.app/home` renders the shell instead of 404.

### CI

- `.github/workflows/ci.yml`:
  - On every PR: install deps, run `supabase start`, run migrations, run `pytest -m 'not smoke'` (includes RLS contract tests).
  - On push to `main`: same as above + `supabase db push` against the production project (using a service-role key stored as a GitHub secret, scoped to the migrations workflow only).

### Smoke test

- `scripts/smoke_prod.py`:
  - Signs in as a dedicated test user (in the prod Supabase project), POSTs a transaction, GETs it back, DELETEs it. Reports pass/fail with timing. Run manually after each deploy until it's worth automating in CI.
- Browser smoke step (manual, documented in README): open `https://tameru.app` in a new incognito window, confirm the shell renders, open devtools → Network, confirm a preflight `OPTIONS` request to `https://api.tameru.app/me` returns 200 with the correct `Access-Control-Allow-Origin`, and the follow-up `GET /me` either succeeds (signed-in session) or cleanly returns 401 (no session) — **no CORS error**, no opaque failure.

### Docs

- `README.md` updated with: backend deploy (Railway), frontend deploy (Vercel), env-var checklist for both, and a link to each dashboard.

## Don't

- Don't commit any real secret to `.env.example` or `railway.json` or `vercel.json`. Examples only.
- Don't enable the `pg_cron` jobs yet — Day 19 (subscriptions) and Day 24 (AICallLog rollup) own those.
- Don't skip the smoke test "because the unit tests passed." Prod has different env vars.
- Don't allow `*.vercel.app` in the CORS config to make preview URLs work against prod API. Previews hit prod API only via an explicit staging backend, which v1 doesn't ship. Preview deploys are for UI review against a localhost API today.

## Done when

- `curl https://api.tameru.app/healthz` returns `{"ok": true}`.
- `curl -i -X OPTIONS https://api.tameru.app/me -H 'Origin: https://tameru.app' -H 'Access-Control-Request-Method: GET'` returns 200 with `Access-Control-Allow-Origin: https://tameru.app`.
- `https://tameru.app` loads the PWA shell from the Vercel edge; Lighthouse PWA audit ≥ 90 against the deployed site.
- `python scripts/smoke_prod.py` passes against prod.
- A PR to `main` triggers CI, RLS contract tests run green, merging deploys both Railway and Vercel without manual steps.
- Killing a request mid-flight and redeploying Railway lets the request finish (grace period works).
