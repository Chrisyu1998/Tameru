# Day 3 — Google OAuth + per-request JWT auth + RLS contract test

## Goal

End-to-end auth: user signs in with Google via Supabase, gets a JWT, FastAPI validates it on every request, and database queries fire RLS via the user's JWT (not service role). Prove it with a contract test.

## Read first

- `DESIGN.md` §9.1 (RLS enforcement path — read carefully), §13.1 (RLS contract tests).
- `CLAUDE.md` invariant 1.

## Deliverables

### `app/auth.py`

- A small dataclass `AuthedUser` with fields `jwt: str`, `user_id: UUID`, `email: str`. This is what dependencies consume downstream — don't return tuples or dicts.
- FastAPI dependency `get_current_user_jwt(request) -> AuthedUser` that:
  - Pulls `Authorization: Bearer <jwt>` off the request.
  - Validates the JWT **locally** against the project's asymmetric JWKS. Use `PyJWT`'s `PyJWKClient` pointed at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. The client caches keys and refreshes on `kid` miss — no per-request round trip to Supabase Auth.
    ```python
    signing_key = jwks_client.get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=f"{SUPABASE_URL}/auth/v1",
    )
    ```
    Pin `algorithms=["ES256"]` — Supabase's signing keys are EC P-256 (verified via the JWKS `kty: EC, alg: ES256`). Accepting `["ES256", "RS256"]` buys nothing and widens the alg-confusion attack surface. Require `issuer` so a token minted by a different Supabase project can't authenticate.
    Do **not** use `supabase-py auth.get_user(jwt)` on the hot path — that round-trips to Supabase Auth on every API call.
  - Returns `AuthedUser(jwt=token, user_id=UUID(claims["sub"]), email=claims["email"])`.
  - Raises `HTTPException(401)` if the header is missing, malformed, expired, or fails signature / audience / issuer verification.
- Why asymmetric: current Supabase CLI versions provision new projects (and the local stack) with ES256 JWT signing keys. `supabase status` still prints a `JWT_SECRET` value but that is legacy — tokens are ES256 with a `kid` and the shared secret is no longer used to sign.
- `SUPABASE_JWT_SECRET` is **not** an env var for this project. Do not add it to `.env.example`. `SUPABASE_URL` is already there; the JWKS URL and issuer are derived from it at import time.
- PyJWT dependency must be installed with the `[crypto]` extra (`pyjwt[crypto]>=2.9` in `pyproject.toml`) — the default install only supports HS*, not ES/RS.

### `app/db.py`

- Function `supabase_for_user(user_jwt: str) -> Client` that returns a per-request Supabase client authorized as the user. **This is the only function application code uses to talk to Supabase.**
  - Implementation note: either construct a fresh `create_client(...)` per request, or use `client.postgrest.auth(user_jwt)` on a pooled client. **Do not** mutate a shared module-level client's auth state — under async concurrency, two requests racing on the same client means user A's query can run with user B's JWT. If in doubt, construct per request; the TLS overhead is small next to the DB round-trip.
- Function `supabase_admin() -> Client` that returns a service-role client. Docstring must say: only `pg_cron` callers (`app/cron/`) and migration scripts (`scripts/`) may import this; application handlers must not.
- `tests/test_no_service_role_leak.py` — a grep-style test that walks `app/` and fails if any file outside `app/cron/` and `scripts/` matches **any** of:
  - `from app.db import supabase_admin`
  - `from app.db import supabase_admin as ` (renamed import)
  - `from app.db import *` (wildcard — bans the whole module to prevent laundering)
  - The literal string `SUPABASE_SERVICE_ROLE_KEY`
  This is test-based, not a real linter — it runs in pytest/CI. Fine for now; upgrade to a ruff rule later if it ever false-positives.

### `app/main.py`

- `GET /me` endpoint protected by `get_current_user_jwt`. Returns `{"user_id": ..., "email": ...}` **directly from the JWT claims** — no DB round-trip, no `users_meta` SELECT. The JWT already has what we need. Day 9 extends this endpoint to also include `home_currency` (adding one `users_meta` SELECT), which is needed to render currency symbols in the UI; accept that extra round-trip when it lands. Not today.

### OAuth configuration

- Google OAuth provider enabled in the Supabase dashboard. Redirect URI documented in `README.md` under a "Google OAuth setup" section.
- `SUPABASE_URL` is already in `.env.example`; the JWKS URL and issuer are derived from it at import time in `app/auth.py`. Fail fast if `SUPABASE_URL` is unset.

### `tests/test_rls_contract.py`

Target: local Supabase stack only. Tests must **not** point at the hosted project. The test's `conftest.py` should assert `SUPABASE_URL` resolves to a localhost/127.0.0.1 host and refuse to run otherwise — this is a footgun prevention measure, not paranoia.

- Fixtures: seed two users (A and B) into the local stack via `supabase_admin()` + `auth.admin.create_user` in a session-scoped fixture, then sign each in via password to get a JWT. Clean up on teardown.
- For each RLS-protected, user-owned table — `cards`, `transactions`, `subscriptions`, `merchant_category`, `user_memory`, `mcp_tokens`, `users_meta`:
  - Insert a row as user A via `supabase_for_user(jwt_a)`.
  - As user B via `supabase_for_user(jwt_b)`, run `.select("*")` **without any `user_id` filter in the query** — this is the whole point of the test. RLS must return zero rows even when the app "forgot" the WHERE clause. **Assert: zero rows.**
  - As user B, attempt `.update(...)` on A's row id. **Assert: rejected / zero rows affected.**
- For append-only audit tables — `ai_call_log`, `ai_call_log_daily` — the policy shape is different (SELECT + narrow INSERT on `ai_call_log`; SELECT only on `ai_call_log_daily`; no UPDATE or DELETE anywhere). See CLAUDE.md invariant 14. Contract test:
  - `ai_call_log` SELECT scoping: using `supabase_admin()`, insert one audit row attributed to user A. As user B via `supabase_for_user(jwt_b)`, `.select("*")` without a `user_id` filter. **Assert: zero rows.**
  - `ai_call_log` narrow INSERT — success case: as user A via `supabase_for_user(jwt_a)`, `.insert({user_id: user_a.id, provider: 'anthropic', ...})`. **Assert: row inserted.** This proves the Day 4 `log_ai_call` helper works without service role.
  - `ai_call_log` narrow INSERT — foreign-user rejection: as user A, `.insert({user_id: user_b.id, provider: 'anthropic', ...})`. **Assert: rejected by the `WITH CHECK (user_id = auth.uid())` policy.** This is the protection against a compromised JWT forging rows on another user's account.
  - `ai_call_log` UPDATE/DELETE rejection: as user A on user A's own row, attempt UPDATE and DELETE. **Assert: both rejected** (no policy for those verbs; users cannot scrub their own audit history).
  - `ai_call_log_daily` SELECT scoping: same as `ai_call_log` read test. No INSERT test — no policy exists for INSERT on `ai_call_log_daily` (rollup writes come from the service-role aggregator).

## Don't

- Don't use `SUPABASE_SERVICE_ROLE_KEY` anywhere in `app/` outside `app/cron/` (which doesn't exist yet).
- Don't validate JWTs by calling Supabase Auth per request (`supabase-py auth.get_user`). Verify locally with the shared secret or JWKS.
- Don't add user profile editing today — `/me` is read-only for now.
- Don't have `/me` read `users_meta` — the JWT has `sub` and `email` already.
- Don't mutate `client.postgrest.auth(...)` on a shared module-level client. Per-request client or per-request `.auth(...)` only.
- Don't point RLS contract tests at the hosted Supabase project. Local stack (`supabase start`) only.

## Done when

- `pytest tests/test_rls_contract.py tests/test_no_service_role_leak.py` passes against a fresh `supabase db reset`.
- A bug where you remove `WHERE user_id = ?` from a query still cannot leak data — RLS catches it. (The contract test proves this by design: it never sends `user_id` in its `.select()`.)
- `curl -H "Authorization: Bearer <real-jwt>" localhost:8000/me` returns `{user_id, email}`.
- Tampering with the JWT signature (flip one character) returns 401, not 500 — proves local verification is actually running.
- `Authorization: Bearer ` (empty token) returns 401, not 500.
