# Day 7 — Deploy to Railway + smoke test in production

## Goal

The Week 1 backend is live on Railway with all secrets in env vars, CI runs RLS contract tests on every PR, and a smoke test exercises the prod deployment end-to-end.

## Read first

- `DESIGN.md` §5.3 (why Railway), §7.5 (Railway grace period — set today), §13 (testing strategy), §14 (observability + ops).

## Deliverables

- Railway project created. The repo's `main` branch deploys on push.
- All env vars set in Railway dashboard:
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
  - `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.1-flash-lite-preview`
  - `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-haiku-4-5` (placeholder; used Week 3)
  - `PERPLEXITY_API_KEY` (placeholder; used Day 11)
  - `SENTRY_DSN` (placeholder; wired Day 24)
- `terminationGracePeriodSeconds: 60` configured in `railway.json` (or service settings).
- `.github/workflows/ci.yml`:
  - On every PR: install deps, run `supabase start`, run migrations, run `pytest -m 'not smoke'` (includes RLS contract tests).
  - On push to `main`: same as above + `supabase db push` against the production project (using a service-role key stored as a GitHub secret, scoped to the migrations workflow only).
- `scripts/smoke_prod.py`:
  - Signs in as a dedicated test user (in the prod Supabase project), POSTs a transaction, GETs it back, DELETEs it. Reports pass/fail with timing. Run manually after each deploy until it's worth automating in CI.
- A custom subdomain (e.g. `api.tameru.app` if `tameru.app` is registered, otherwise the Railway-generated one) bound to the service.
- `README.md` updated with: deploy instructions, env var checklist, link to Railway dashboard.

## Don't

- Don't commit any real secret to `.env.example` or `railway.json`. Examples only.
- Don't enable the `pg_cron` jobs yet — Day 14 (subscriptions) and Day 24 (AICallLog rollup) own those.
- Don't skip the smoke test "because the unit tests passed." Prod has different env vars.

## Done when

- `curl https://<your-railway-url>/healthz` returns `{"ok": true}`.
- `python scripts/smoke_prod.py` passes against prod.
- A PR to `main` triggers CI, RLS contract tests run green, merging deploys.
- Killing a request mid-flight and redeploying lets the request finish (grace period works).
