"""Admin AI-call summary — `GET /admin/aicalls/summary` (Day 24 + per-user).

Covers the Day-24 admittance contract and the per-user breakdown added
on top of it:

  1. A non-admin caller gets 404 (endpoint disclosure minimization).
  2. The default (un-grouped) summary leaves `user_id` null — unchanged
     wire shape.
  3. `group_by_user=true` attributes tokens per user and surfaces the
     heavier user first (the whole point of the breakdown), reading
     across users via the admin SELECT policy on `ai_call_log`.

These hit the real local Supabase stack: admin membership is granted
out-of-band via the service-role `admin_client` (the table has no
end-user INSERT policy), and each test cleans the window it seeds so a
shared session-scoped user doesn't carry rows between tests.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app


@pytest.fixture
def http_client() -> TestClient:
    """A TestClient bound to the app (bare form — no lifespan/boot check)."""
    return TestClient(app)


@pytest.fixture
def admin_user_a(admin_client, user_a):
    """Grant user_a admin via service role for the test, then revoke.

    The `admins` table has no end-user INSERT policy, so membership is
    managed out-of-band exactly as production does it (Supabase SQL
    Editor / service role). Revoked on teardown so other tests that
    share the session-scoped user_a don't inherit admin rights.
    """
    admin_client.table("admins").upsert({"user_id": user_a.id}).execute()
    yield user_a
    admin_client.table("admins").delete().eq("user_id", user_a.id).execute()


def test_non_admin_gets_404(http_client, user_b):
    """A caller absent from `admins` cannot tell the route exists."""
    resp = http_client.get("/admin/aicalls/summary", headers=_auth(user_b))
    assert resp.status_code == 404


def test_default_summary_omits_user_id(http_client, admin_user_a):
    """Without `group_by_user` the rows omit `user_id` entirely — the
    response stays byte-identical to its pre-`user_id` shape (the field
    is excluded when null via `response_model_exclude_none`)."""
    resp = http_client.get(
        "/admin/aicalls/summary?days=1", headers=_auth(admin_user_a)
    )
    assert resp.status_code == 200
    for row in resp.json()["rows"]:
        assert "user_id" not in row


@pytest.fixture
def isolated_aicall_rows(admin_client, user_a, user_b):
    """Wipe both users' recent ai_call_log rows around the test.

    Critical for the shared session-scoped users: the RLS contract suite
    asserts user_b's ai_call_log is empty, so any chat_turn rows this
    test seeds must not survive it. Cleans before (known-zero baseline)
    and after (no pollution of later tests).
    """
    _clean_recent(admin_client, user_a.id)
    _clean_recent(admin_client, user_b.id)
    yield
    _clean_recent(admin_client, user_a.id)
    _clean_recent(admin_client, user_b.id)


def test_group_by_user_attributes_tokens_and_orders_by_weight(
    http_client, admin_user_a, user_a, user_b, isolated_aicall_rows
):
    """Per-user view sums tokens per user and sorts the heaviest first.

    Seeds user_b as the heavier user; asserts both users' chat_turn
    totals match what was seeded and that user_b's row precedes user_a's
    in the globally token-sorted result.
    """
    _seed_chat_turn(user_a, input_tokens=100, output_tokens=50)
    _seed_chat_turn(user_b, input_tokens=1000, output_tokens=500)

    resp = http_client.get(
        "/admin/aicalls/summary?days=1&group_by_user=true",
        headers=_auth(admin_user_a),
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]

    # `.get` not `[]`: an orphaned-rows bucket (ai_call_log.user_id is
    # ON DELETE SET NULL) has a null user_id, which `response_model_exclude_none`
    # omits from the row entirely — indexing would KeyError on it.
    a_rows = [r for r in rows if r.get("user_id") == user_a.id and r["task_type"] == "chat_turn"]
    b_rows = [r for r in rows if r.get("user_id") == user_b.id and r["task_type"] == "chat_turn"]
    assert len(a_rows) == 1 and len(b_rows) == 1
    assert a_rows[0]["sum_input_tokens"] == 100
    assert a_rows[0]["sum_output_tokens"] == 50
    assert b_rows[0]["sum_input_tokens"] == 1000
    assert b_rows[0]["sum_output_tokens"] == 500

    # Heavier user surfaces first in the token-desc ordering.
    assert rows.index(b_rows[0]) < rows.index(a_rows[0])


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Bearer header for a fixture user's JWT."""
    return {"Authorization": f"Bearer {user.jwt}"}


def _clean_recent(admin_client, user_id: str) -> None:
    """Delete the user's ai_call_log rows from the last day via service role.

    ai_call_log has no end-user DELETE policy (audit history is
    unscrubbable, invariant 14), so cleanup goes through admin_client —
    the same pattern the cap tests use.
    """
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).isoformat()
    admin_client.table("ai_call_log").delete().eq("user_id", user_id).gte(
        "timestamp", cutoff
    ).execute()


def _seed_chat_turn(user, *, input_tokens: int, output_tokens: int) -> None:
    """Insert one `chat_turn` row under the user's own JWT (RLS WITH CHECK)."""
    client = supabase_for_user(user.jwt)
    client.table("ai_call_log").insert({
        "user_id": user.id,
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "task_type": "chat_turn",
        "prompt_version": "chat_v12",
        "prompt_hash": "x" * 64,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": 1,
        "success": True,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }).execute()
