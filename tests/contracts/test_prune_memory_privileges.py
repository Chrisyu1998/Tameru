"""Contract: service-role-only SECURITY DEFINER functions are unreachable from end-user JWTs.

These functions perform cross-user sweeps or system-level writes with no
`auth.uid()` guard. Only `service_role` (cron / manual ops) may invoke
them; an end-user JWT must be denied.

This test exists because a migration's `REVOKE EXECUTE ... FROM PUBLIC`
is *not* enough on its own — `20260515210000_backfill_supabase_grants.sql`
sets `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO
anon, authenticated, service_role`, so newly created functions auto-grant
EXECUTE to `anon` and `authenticated`. Each migration must explicitly
`REVOKE EXECUTE ... FROM anon, authenticated` to strip those default
grants (memory.md 2026-05-18).

Originally this file pinned only `prune_user_memory`, and its docstring
predicted the failure mode that then materialized: `autolog_subscriptions`
shipped with a PUBLIC-only revoke and stayed callable by anon and
authenticated until the 2026-06 audit (fixed in migration
20260610120000_revoke_definer_function_grants.sql). The test is now
parametrized over every service-role-only DEFINER function so the next
one can't slip through. When a new service-role-only DEFINER function
ships, add it to `SERVICE_ROLE_ONLY_FUNCTIONS` in the same change.
"""

from __future__ import annotations

import pytest

from app.db import supabase_for_user

# Function name -> RPC args that match its signature. Args must resolve to
# a real function signature so PostgREST gets far enough to check EXECUTE
# privilege — a signature mismatch raises "function not found", which
# would falsely satisfy the negative test even with the grant in place.
SERVICE_ROLE_ONLY_FUNCTIONS: dict[str, dict] = {
    "prune_user_memory": {},
    "trim_eval_user_ai_call_log": {},
    "aggregate_aicalllog": {},
    "autolog_subscriptions": {},
    "email_log_insert_idempotent": {
        "p_user_id": "00000000-0000-0000-0000-000000000000",
        "p_kind": "digest",
        "p_success": False,
        "p_provider_message_id": None,
        "p_error_code": "contract_test",
        "p_dedup_week": None,
    },
}


@pytest.mark.parametrize("fn_name", sorted(SERVICE_ROLE_ONLY_FUNCTIONS))
def test_authenticated_cannot_execute_service_role_only_function(fn_name, user_a):
    """Calling a service-role-only DEFINER function via a user JWT must be denied.

    PostgREST surfaces a missing-EXECUTE-grant as a `42501` permission
    error wrapped in the supabase-py exception text. We don't pin the
    exact message (PostgREST + supabase-py reword these across versions)
    — only that the call raises with a permission-shaped error.
    """
    client = supabase_for_user(user_a.jwt)
    with pytest.raises(Exception) as excinfo:
        client.rpc(fn_name, SERVICE_ROLE_ONLY_FUNCTIONS[fn_name]).execute()

    err = str(excinfo.value).lower()
    assert (
        "permission" in err
        or "denied" in err
        or "not allow" in err
        or "42501" in err
    ), f"Unexpected error shape for {fn_name}: {excinfo.value!r}"


@pytest.mark.parametrize(
    "fn_name",
    ["prune_user_memory", "trim_eval_user_ai_call_log", "aggregate_aicalllog", "autolog_subscriptions"],
)
def test_service_role_can_execute_service_role_only_function(fn_name, admin_client):
    """Sanity-check the positive case: service_role retains EXECUTE.

    Without this assertion, a future "REVOKE FROM service_role too" typo
    would also pass the negative test above and we'd quietly break cron.
    All four are no-ops at this point in the session (contracts collect
    before the suites that seed due subscriptions / 90-day-old ai_call_log
    rows), so calling them has no side effects on later tests.

    `email_log_insert_idempotent`'s positive path is deliberately covered
    by its own write-asserting test below rather than a bare smoke call —
    it takes required args and actually inserts.
    """
    admin_client.rpc(fn_name, {}).execute()


def test_service_role_can_execute_email_log_insert(admin_client, user_a):
    """Positive case for the one service-role-only function with required args.

    Inserts a `success=False` row (excluded from the weekly dedup partial
    index, invisible to product reads) and deletes it again so no residue
    leaks into the digest suites.
    """
    args = {
        "p_user_id": str(user_a.id),
        "p_kind": "digest",
        "p_success": False,
        "p_provider_message_id": None,
        "p_error_code": "contract_test",
        "p_dedup_week": None,
    }
    inserted = admin_client.rpc("email_log_insert_idempotent", args).execute()
    try:
        assert inserted.data, "expected the idempotent insert to return the new row"
    finally:
        for row in inserted.data or []:
            admin_client.table("email_log").delete().eq("id", row["id"]).execute()
