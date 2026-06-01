"""Day 7 — `app/routes/auth.py` contract.

Covers the three /auth/* endpoints:
- POST /auth/bootstrap: happy path, 409 on second call, 422 on bad currency.
- POST /auth/claim_device: happy path (active_device_id flips), 409 when no
  users_meta row exists yet.
- GET /auth/check_device: is_active true/false depending on header match.

The single-active-device dependency itself (`get_current_user_with_device`,
which lives in `app/auth.py`) is tested in `tests/test_auth.py` — those
tests exercise it via the gated transactions routes.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provide client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# /auth/bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_creates_users_meta_row(client, user_unbootstrapped):
    """Verify that bootstrap creates users meta row."""
    device_id = f"dev-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": device_id, "home_currency": "EUR"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Day 29: timezone rides on the bootstrap response; null when the client
    # didn't send one (DESIGN.md §6.6).
    assert body == {
        "home_currency": "EUR",
        "active_device_id": device_id,
        "timezone": None,
    }

    # And the row really landed under the user's RLS scope.
    db = supabase_for_user(user_unbootstrapped.jwt)
    row = (
        db.table("users_meta")
        .select("home_currency, active_device_id")
        .eq("user_id", user_unbootstrapped.id)
        .execute()
        .data
    )
    assert row == [{"home_currency": "EUR", "active_device_id": device_id}]


def test_bootstrap_stores_valid_timezone(client, user_unbootstrapped):
    """A valid IANA timezone sent at bootstrap is persisted and echoed back
    (DESIGN.md §6.6 — decoupled from home_currency)."""
    device_id = f"dev-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={
            "device_id": device_id,
            "home_currency": "JPY",
            "timezone": "Asia/Tokyo",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Asia/Tokyo"

    db = supabase_for_user(user_unbootstrapped.jwt)
    row = (
        db.table("users_meta")
        .select("timezone")
        .eq("user_id", user_unbootstrapped.id)
        .execute()
        .data
    )
    assert row == [{"timezone": "Asia/Tokyo"}]


def test_bootstrap_rejects_invalid_timezone(client, user_unbootstrapped):
    """A non-IANA timezone is rejected at the API boundary (422) rather than
    written and silently breaking the digest cron."""
    device_id = f"dev-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={
            "device_id": device_id,
            "home_currency": "USD",
            "timezone": "Mars/Phobos",
        },
    )
    assert resp.status_code == 422, resp.text


def test_bootstrap_second_call_returns_409(client, user_unbootstrapped):
    """Verify that bootstrap second call returns 409."""
    device_id = f"dev-{uuid.uuid4().hex[:8]}"
    first = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": device_id, "home_currency": "USD"},
    )
    assert first.status_code == 200

    second = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": device_id, "home_currency": "USD"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_bootstrapped"


def test_bootstrap_with_different_currency_after_bootstrap_returns_409(
    client, user_unbootstrapped
):
    """Currency-change attempt is structurally impossible from the API
    surface — bootstrap is one-shot, regardless of whether the currency
    differs from the existing row. No path to mutate home_currency exists
    in v1 (CLAUDE.md invariant 13)."""
    client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": "dev-1", "home_currency": "USD"},
    )
    resp = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": "dev-1", "home_currency": "EUR"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_bootstrapped"


def test_bootstrap_rejects_unsupported_currency(client, user_unbootstrapped):
    """Verify that bootstrap rejects unsupported currency."""
    resp = client.post(
        "/auth/bootstrap",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": "dev-x", "home_currency": "XYZ"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_home_currency"


def test_bootstrap_requires_jwt(client):
    """Verify that bootstrap requires jwt."""
    resp = client.post(
        "/auth/bootstrap",
        json={"device_id": "dev-1", "home_currency": "USD"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /auth/claim_device
# ---------------------------------------------------------------------------


def test_claim_device_updates_active_device_id(client, user_a):
    """Verify that claim device updates active device id."""
    new_device = f"dev-claim-{uuid.uuid4().hex[:8]}"
    try:
        resp = client.post(
            "/auth/claim_device",
            headers=_bearer(user_a),
            json={"device_id": new_device},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"active_device_id": new_device}
    finally:
        # Restore the fixture's device_id so other tests in the same
        # session see the canonical value, even if the assertion above
        # fails. Without `finally`, a failure here would cascade into
        # every subsequent device-gated test.
        client.post(
            "/auth/claim_device",
            headers=_bearer(user_a),
            json={"device_id": user_a.device_id},
        )


def test_claim_device_without_bootstrap_returns_409(client, user_unbootstrapped):
    """Verify that claim device without bootstrap returns 409."""
    resp = client.post(
        "/auth/claim_device",
        headers=_bearer(user_unbootstrapped),
        json={"device_id": "dev-x"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "not_bootstrapped"


# ---------------------------------------------------------------------------
# /auth/check_device
# ---------------------------------------------------------------------------


def test_check_device_matches(client, user_a):
    """Verify that check device matches."""
    resp = client.get(
        "/auth/check_device",
        headers=_bearer(user_a),
        params={"device_id": user_a.device_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is True
    assert body["active_device_id"] == user_a.device_id


def test_check_device_mismatch_returns_inactive(client, user_a):
    """Verify that check device mismatch returns inactive."""
    resp = client.get(
        "/auth/check_device",
        headers=_bearer(user_a),
        params={"device_id": "different-device"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is False
    assert body["active_device_id"] == user_a.device_id


def test_check_device_unbootstrapped_returns_inactive(client, user_unbootstrapped):
    """No row → is_active=false, active_device_id=null. Frontend treats
    this the same as a mismatch."""
    resp = client.get(
        "/auth/check_device",
        headers=_bearer(user_unbootstrapped),
        params={"device_id": "dev-anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is False
    assert body["active_device_id"] is None


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _bearer(user) -> dict[str, str]:
    """Support bearer."""
    return {"Authorization": f"Bearer {user.jwt}"}
