# Day 3 — Google OAuth + per-request JWT auth + RLS contract test

## Goal

End-to-end auth: user signs in with Google via Supabase, gets a JWT, FastAPI validates it on every request, and database queries fire RLS via the user's JWT (not service role). Prove it with a contract test.

## Read first

- `DESIGN.md` §9.1 (RLS enforcement path — read carefully), §13.1 (RLS contract tests).
- `CLAUDE.md` invariant 1.

## Deliverables

- `app/auth.py`:
  - FastAPI dependency `get_current_user_jwt(request)` that pulls `Authorization: Bearer <jwt>`, validates it against Supabase's JWKS (or via the `supabase-py` client), returns the JWT string and decoded `user_id`.
  - Raises 401 if missing/invalid.
- `app/db.py`:
  - Function `supabase_for_user(user_jwt: str) -> Client` that returns a per-request Supabase client initialized with that JWT. **This is the only function application code uses to talk to Supabase.**
  - A separate function `supabase_admin() -> Client` that returns a service-role client. Document in a docstring: only `pg_cron` callers and migration scripts may import this. Add a CI lint (or a clear comment + grep test in `tests/`) that flags `supabase_admin()` imports outside `app/cron/` and `scripts/`.
- `app/main.py`:
  - `GET /me` endpoint that returns `{user_id, email}` from the JWT. Protected by `get_current_user_jwt`.
- Google OAuth provider configured in the Supabase dashboard (or via CLI). Redirect URI documented in README.
- `tests/test_rls_contract.py`:
  - Two test fixtures sign in as user A and user B (use Supabase test users — sign in via password or seeded JWTs).
  - Insert a row into `cards` as user A.
  - Attempt to read it as user B. **Assert: zero rows.**
  - Attempt to update it as user B. **Assert: rejected.**
  - Repeat the pattern for `transactions`, `subscriptions`, `merchant_category`, `user_memory`, `mcp_tokens`.

## Don't

- Don't use `SUPABASE_SERVICE_ROLE_KEY` anywhere in `app/` outside `app/cron/` (which doesn't exist yet).
- Don't roll your own JWT validation if `supabase-py` already does it — use the library.
- Don't add user profile editing today — `/me` is read-only for now.

## Done when

- `pytest tests/test_rls_contract.py` passes.
- A bug where you remove `WHERE user_id = ?` from a query still cannot leak data — RLS catches it.
- `curl -H "Authorization: Bearer <real-jwt>" localhost:8000/me` returns the user.
