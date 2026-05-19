"""Contract: `prune_user_memory()` is unreachable from end-user JWTs.

The function is `SECURITY DEFINER` and performs a cross-user sweep of
`user_memory`. Only `service_role` (manual ops / cron) and the
pg_cron-internal caller should be able to invoke it.

This test exists because the migration's `REVOKE EXECUTE ... FROM
PUBLIC` is *not* enough on its own — `20260515210000_backfill_supabase_grants.sql`
sets `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO
anon, authenticated, service_role`, so newly created functions auto-grant
EXECUTE to `anon` and `authenticated`. The Day 17 migration must
explicitly `REVOKE EXECUTE ... FROM anon, authenticated` to strip those
default grants. If a future migration adds a similarly sensitive
DEFINER function and forgets the explicit REVOKE, this test stays green
(it only tests one function) — but the same pattern audit applies.

Codex review caught the missing REVOKE; this test pins the fix so a
regression in the migration is caught on every CI run.
"""

from __future__ import annotations

import pytest

from app.db import supabase_for_user


def test_authenticated_cannot_execute_prune_user_memory(user_a):
    """Calling `prune_user_memory` via a user JWT must be denied.

    PostgREST surfaces a missing-EXECUTE-grant as a `42501` permission
    error wrapped in the supabase-py exception text. We don't pin the
    exact message (PostgREST + supabase-py reword these across versions)
    — only that the call raises.
    """
    client = supabase_for_user(user_a.jwt)
    with pytest.raises(Exception) as excinfo:
        client.rpc("prune_user_memory", {}).execute()

    err = str(excinfo.value).lower()
    # The error must mention the function or a permission/auth signal so
    # a future "function returns 0 rows but doesn't raise" regression is
    # caught — a silent success would imply the grant slipped back in.
    assert (
        "prune_user_memory" in err
        or "permission" in err
        or "denied" in err
        or "not allow" in err
        or "42501" in err
    ), f"Unexpected error shape: {excinfo.value!r}"


def test_service_role_can_execute_prune_user_memory(admin_client):
    """Sanity-check the positive case: service_role retains EXECUTE.

    The admin client uses the service_role key, which is the sanctioned
    caller for manual ops and the path the cron job effectively
    exercises (cron runs as superuser but bypasses RLS the same way).
    Without this assertion, a future "REVOKE FROM service_role too"
    typo would also pass the test above and we'd quietly break cron.
    """
    # No-op against an empty DB but must not raise.
    admin_client.rpc("prune_user_memory", {}).execute()
