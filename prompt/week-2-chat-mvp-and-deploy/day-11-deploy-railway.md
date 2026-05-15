# Day 11 — Deploy backend to Railway + frontend to Vercel + smoke test in production

## Goal

The Week 1 backend is live on Railway with all secrets in env vars, the frontend PWA from Day 6 is live on Vercel pointing at the Railway API, CORS is verified cross-origin, CI runs RLS contract tests on every PR, and a smoke test exercises the prod deployment end-to-end.

## Read first

- `DESIGN.md` §5.3 (hosting split: Railway backend + Vercel frontend), §7.5 (Railway grace period — set today), §9.3 (CORS, Bearer-token auth), §13 (testing strategy), §14 (observability + ops).
- `CLAUDE.md` invariant 1 (service role is reserved for `pg_cron` and CLI migrations — do **not** put it in the Railway runtime env until a sanctioned in-process caller exists).

## Host placeholders

Throughout this prompt, `BACKEND_HOST` and `FRONTEND_HOST` are placeholders. If you've registered `tameru.app`, use `api.tameru.app` and `tameru.app`. Otherwise use the platform-generated URLs (`*.up.railway.app` and `*.vercel.app`) verbatim — every command, env var, and CORS rule must substitute the **actual** hostnames you end up with, including `https://` scheme. Don't leave any literal `tameru.app` in your config if you don't own that domain.

## Deliverables

### Backend (Railway)

- Railway project created. The repo's `main` branch deploys on push.
- All env vars set in Railway dashboard:
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`
  - **Do not set `SUPABASE_SERVICE_ROLE_KEY` in Railway today.** Per `CLAUDE.md` invariant 1, the service role has exactly two sanctioned callers — `pg_cron` (runs inside Postgres, no Railway env in scope) and Supabase CLI migrations (CI context). No FastAPI handler currently calls `supabase_admin()`, so adding the key to Railway's runtime env only widens the blast radius of a compromised handler. The day this changes (e.g., a Railway-resident admin caller in `app/cron/` ships), add the key in the same PR that introduces the caller, with a comment.
  - `GEMINI_API_KEY`, `GEMINI_MODEL_DEFAULT=gemini-2.5-flash` — v1 production default per `CLAUDE.md` ("Model usage by task") and `.env.example`. **Do not set `GEMINI_MODEL`** in Railway; that env var is reserved as a per-process override for eval experiments. The preview model `gemini-3.1-flash-lite-preview` 503s intermittently and is not the prod default.
  - `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-haiku-4-5` (placeholder; used Week 3)
  - `PERPLEXITY_API_KEY` (placeholder; used Day 14)
  - `SENTRY_DSN` (placeholder; wired Day 24)
  - `FRONTEND_ORIGIN=https://<FRONTEND_HOST>` — consumed by Day 6's `CORSMiddleware` (`app/main.py:53–63`) to allowlist the Vercel origin in production. Must include scheme; no trailing slash.
- `terminationGracePeriodSeconds: 60` configured in `railway.json` (or service settings).
- Custom subdomain `api.<your-domain>` bound to the Railway service if you own a domain; otherwise use the Railway-generated `*.up.railway.app` URL and update `FRONTEND_ORIGIN` and Vercel's `VITE_API_URL` accordingly.

### Frontend (Vercel)

- Vercel project created, linked to the GitHub repo with `frontend/` as the project root. `main` branch deploys on push; PRs get preview URLs.
- Vercel env vars set (production scope):
  - `VITE_API_URL=https://<BACKEND_HOST>` — **baked into the JS bundle at build time, not read at runtime.** Changing the backend hostname later requires a Vercel rebuild + redeploy, not just an env edit and reload. Document this in the README so future-you doesn't get stuck debugging stale URLs.
  - `VITE_SUPABASE_URL`
  - `VITE_SUPABASE_ANON_KEY`
- Custom domain bound to Vercel if you own one; otherwise the `*.vercel.app` URL is the canonical frontend.
- `frontend/vercel.json` (from Day 6) is what makes client-side routing resolve on direct URL visits — confirm that a direct visit to `https://<FRONTEND_HOST>/home` renders the shell instead of 404.

### CI

- `.github/workflows/ci.yml`:
  - **On every PR:** install deps, run `supabase start`, run migrations, run `pytest -m 'not smoke'` (includes RLS contract tests).
  - **On push to `main`:** run the test job first; on green, run `supabase db push` against the production project; **only after migrations succeed**, signal Railway to deploy (Railway's GitHub integration auto-deploys on push, so the ordering trick is to gate migrations as a required check that runs before the deploy hook fires — or, simpler for a solo dev, disable Railway's auto-deploy on push and trigger it via a final `railway up` step in the workflow after `supabase db push` succeeds). The point of the ordering: if new code expects a new column, the migration must land first or every request 500s for the deploy window.
  - **GitHub Secrets needed for the migrations job:**
    - `SUPABASE_ACCESS_TOKEN` — personal access token from the Supabase dashboard (Account → Access Tokens). This is what `supabase login`/`supabase db push` authenticates with. **Not** the service role key — `supabase db push` does not accept the service role key for auth.
    - `SUPABASE_DB_PASSWORD` — the prod project's Postgres password (Project Settings → Database → Connection string). `supabase db push` needs this to open the direct Postgres connection that applies the migration.
    - `SUPABASE_PROJECT_REF` — the prod project ref (the `abcdefgh` part of `abcdefgh.supabase.co`).
  - Scope these secrets to the migrations workflow only (GitHub Environments → `production` → secret scoping).

### Smoke test

- `scripts/smoke_prod.py`:
  - Signs in as a dedicated test user (in the prod Supabase project), POSTs a transaction, GETs it back, DELETEs it. Reports pass/fail with timing. Run manually after each deploy until it's worth automating in CI.
- Browser smoke step (manual, documented in README): open `https://<FRONTEND_HOST>` in a new incognito window, confirm the shell renders, open devtools → Network, confirm a preflight `OPTIONS` request to `https://<BACKEND_HOST>/me` returns 200 with the correct `Access-Control-Allow-Origin`, and the follow-up `GET /me` either succeeds (signed-in session) or cleanly returns 401 (no session) — **no CORS error**, no opaque failure.

### Docs

- `README.md` updated with: backend deploy (Railway), frontend deploy (Vercel), env-var checklist for both, the `VITE_API_URL` rebuild-on-change note, and a link to each dashboard.

## Don't

- Don't commit any real secret to `.env.example` or `railway.json` or `vercel.json`. Examples only.
- Don't enable the `pg_cron` jobs yet — Day 19 (subscriptions) and Day 24 (AICallLog rollup) own those.
- Don't skip the smoke test "because the unit tests passed." Prod has different env vars.
- Don't allow `*.vercel.app` in the CORS config to make preview URLs work against prod API. Previews hit prod API only via an explicit staging backend, which v1 doesn't ship. Preview deploys are for UI review against a localhost API today.
- Don't put `SUPABASE_SERVICE_ROLE_KEY` in the Railway runtime env (see invariant 1 above).
- Don't put `GEMINI_MODEL` in the Railway runtime env. That slot is the eval override; production reads `GEMINI_MODEL_DEFAULT`.
- Don't authenticate `supabase db push` with the service role key. Use `SUPABASE_ACCESS_TOKEN` + `SUPABASE_DB_PASSWORD`.

## Done when

Substitute your real hostnames in the commands below.

- `curl https://<BACKEND_HOST>/healthz` returns `{"ok": true}`.
- `curl -i -X OPTIONS https://<BACKEND_HOST>/me -H 'Origin: https://<FRONTEND_HOST>' -H 'Access-Control-Request-Method: GET'` returns 200 with `Access-Control-Allow-Origin: https://<FRONTEND_HOST>`.
- `https://<FRONTEND_HOST>` loads the PWA shell from the Vercel edge; Lighthouse PWA audit ≥ 90 against the deployed site.
- `python scripts/smoke_prod.py` passes against prod.
- A PR to `main` triggers CI, RLS contract tests run green, merging applies migrations **before** the Railway deploy fires and both Railway and Vercel finish without manual steps.
- Killing a request mid-flight and redeploying Railway lets the request finish (grace period works).
