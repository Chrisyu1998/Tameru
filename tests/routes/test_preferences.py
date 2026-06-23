"""PATCH /me/preferences — user-toggleable users_meta columns (Day 25).

Exercises the user-JWT path that's the third co-equal opt-out path
alongside the one-click unsubscribe and the Resend webhook. RLS owner-
UPDATE policy scopes the write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app

# Failure-path cleanup: restore user_a's shared users_meta prefs even when asserts fail (audit P3-37).
pytestmark = pytest.mark.usefixtures("preserve_user_a_meta")



@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI test client."""
    return TestClient(app)


def test_patch_flips_weekly_digest(client, user_a):
    """PATCH with weekly_digest_enabled=false flips the column and returns canonical state."""
    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()

    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"weekly_digest_enabled": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["weekly_digest_enabled"] is False
    # Day 26 expanded the response shape to include analytics_opted_out;
    # the unrelated column must round-trip unchanged.
    assert "analytics_opted_out" in body

    after = (
        db.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert after["weekly_digest_enabled"] is False

    # Cleanup: restore for downstream tests.
    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()


def test_patch_flips_analytics_opted_out(client, user_a):
    """PATCH with analytics_opted_out=true flips the column under the user's JWT (Day 26).

    Owner-UPDATE RLS scopes the write; no service-role path. The
    response carries the canonical post-write state of all preference
    columns so the frontend can drop optimistic UI state without a
    follow-up read.
    """
    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update({"analytics_opted_out": False}).eq(
        "user_id", user_a.id
    ).execute()

    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"analytics_opted_out": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["analytics_opted_out"] is True

    after = (
        db.table("users_meta")
        .select("analytics_opted_out")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert after["analytics_opted_out"] is True

    # Cleanup: restore for downstream tests.
    db.table("users_meta").update({"analytics_opted_out": False}).eq(
        "user_id", user_a.id
    ).execute()


def test_patch_empty_body_returns_state_no_write(client, user_a):
    """Empty PATCH is a read — returns canonical state without writing.

    The frontend uses this as a cheap read on Settings page mount;
    avoids adding a separate GET endpoint for one boolean.
    """
    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update(
        {"weekly_digest_enabled": True, "analytics_opted_out": False}
    ).eq("user_id", user_a.id).execute()

    resp = client.patch("/me/preferences", headers=_headers(user_a), json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["weekly_digest_enabled"] is True
    assert body["analytics_opted_out"] is False


def test_patch_sets_valid_timezone(client, user_a):
    """A valid IANA timezone is persisted and round-trips in the response
    (Day 29, DESIGN.md §6.6). Timezone is mutable, unlike home_currency."""
    db = supabase_for_user(user_a.jwt)
    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"timezone": "Asia/Taipei"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Asia/Taipei"

    after = (
        db.table("users_meta")
        .select("timezone")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert after["timezone"] == "Asia/Taipei"

    # Cleanup: clear for downstream tests.
    db.table("users_meta").update({"timezone": None}).eq(
        "user_id", user_a.id
    ).execute()


def test_patch_rejects_invalid_timezone(client, user_a):
    """A non-IANA timezone is rejected (422) before any write."""
    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"timezone": "Not/AZone"},
    )
    assert resp.status_code == 422


def test_patch_sets_valid_ui_language(client, user_a):
    """A supported ui_language is persisted and round-trips (Day 29 Tier 2,
    DESIGN.md §6.6). Mutable, like timezone; the third i18n axis."""
    db = supabase_for_user(user_a.jwt)
    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"ui_language": "ja"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ui_language"] == "ja"

    after = (
        db.table("users_meta")
        .select("ui_language")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert after["ui_language"] == "ja"

    # Cleanup: clear for downstream tests.
    db.table("users_meta").update({"ui_language": None}).eq(
        "user_id", user_a.id
    ).execute()


def test_patch_rejects_invalid_ui_language(client, user_a):
    """An unsupported language code is rejected (422) before any write —
    zh-CN (Simplified) is deliberately out of scope, so it must 422."""
    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"ui_language": "zh-CN"},
    )
    assert resp.status_code == 422


def test_patch_unknown_field_rejected(client, user_a):
    """Unknown fields fail validation (extra='forbid' on the model)."""
    resp = client.patch(
        "/me/preferences",
        headers=_headers(user_a),
        json={"some_unknown_field": True},
    )
    assert resp.status_code == 422


def test_patch_requires_auth(client):
    """No bearer token → 401."""
    resp = client.patch("/me/preferences", json={"weekly_digest_enabled": False})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _headers(user) -> dict:
    """Auth + device-id headers for the gated PATCH."""
    return {"Authorization": f"Bearer {user.jwt}", "X-Device-Id": user.device_id}
