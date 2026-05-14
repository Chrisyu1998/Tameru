"""The only sanctioned way for application code to reach Supabase.

`supabase_for_user` returns a per-request client authorized with the user's
JWT so PostgREST forwards it to Postgres and RLS fires on `auth.uid()`
(CLAUDE.md invariant 1, DESIGN.md §9.1). `supabase_admin` returns a
service-role client and is reserved for `pg_cron` callers and migrations.

Concurrency: we construct a fresh client per call rather than mutate a
shared module-level client's auth state, which would race under async
concurrency — two requests on the same process could otherwise see one
user's JWT used for the other's query. `tests/test_no_service_role_leak.py`
enforces the import boundary.
"""

from __future__ import annotations

import os

from supabase import Client, create_client


def supabase_for_user(user_jwt: str) -> Client:
    """Per-request Supabase client authorized as the JWT's subject.

    Use this everywhere in application code. The resulting client runs
    PostgREST queries with `request.jwt.claims` populated, so Postgres
    enforces RLS — a missing `WHERE user_id = ?` cannot leak data.
    """
    url = _require_env("SUPABASE_URL")
    anon_key = _require_env("SUPABASE_ANON_KEY")
    client = create_client(url, anon_key)
    client.postgrest.auth(user_jwt)
    return client


def supabase_admin() -> Client:
    """Service-role Supabase client that bypasses RLS.

    RESTRICTED. Only two callers are permitted:
      1. `app/cron/` — pg_cron-triggered jobs (subscription auto-logger,
         ai_call_log rollup).
      2. `scripts/` — migration and one-off administrative scripts.

    Application request handlers must never import this function.
    `tests/test_no_service_role_leak.py` fails CI if this import appears
    anywhere else in `app/`.
    """
    url = _require_env("SUPABASE_URL")
    service_role_key = _require_env("SUPABASE_SERVICE_ROLE_KEY")
    return create_client(url, service_role_key)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Support require env."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. See .env.example.")
    return value
