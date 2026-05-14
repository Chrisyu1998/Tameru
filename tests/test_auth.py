"""`app/auth.py` contract — JWT verifier + single-active-device dependency.

The bare-JWT verifier (`get_current_user_jwt`) is exercised primarily
through `tests/test_main.py::test_me_*` — those cover the no-header,
bare-bearer, and tampered-signature cases on the unprotected /me path.
This file focuses on the device gate (`get_current_user_with_device`):

- Missing X-Device-Id → 401 MISSING_DEVICE_ID.
- Mismatched X-Device-Id → 401 DEVICE_DISPLACED.
- Matching X-Device-Id → request passes through.
- End-to-end displacement: browser B claims, browser A's next call 401s.

We exercise the dependency through `GET /transactions` rather than
poking the function directly. That route inherits the gate the same way
every other Day-7+ route does, so a regression in the dependency surfaces
here exactly as it would in production traffic.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provide client."""
    return TestClient(app)


def test_protected_route_without_device_header_returns_401(client, user_a):
    """Verify that protected route without device header returns 401."""
    resp = client.get("/transactions", headers=_bearer(user_a))
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MISSING_DEVICE_ID"


def test_protected_route_with_wrong_device_returns_displaced(client, user_a):
    """Verify that protected route with wrong device returns displaced."""
    headers = {**_bearer(user_a), "X-Device-Id": "this-is-not-the-active-device"}
    resp = client.get("/transactions", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "DEVICE_DISPLACED"


def test_protected_route_with_matching_device_passes(client, user_a):
    """Verify that protected route with matching device passes."""
    headers = {**_bearer(user_a), "X-Device-Id": user_a.device_id}
    resp = client.get("/transactions", headers=headers)
    assert resp.status_code == 200


def test_browser_a_displaced_after_browser_b_claim(client, user_a):
    """End-to-end displacement (Day 7 'done when'). Browser A is the
    active device from the fixture; browser B claims; A's next call 401s
    with DEVICE_DISPLACED. We restore A's claim in `finally` so the
    session-scoped fixture stays usable even on assertion failure."""
    browser_a = user_a.device_id
    browser_b = f"dev-browser-b-{uuid.uuid4().hex[:8]}"

    try:
        claim = client.post(
            "/auth/claim_device",
            headers=_bearer(user_a),
            json={"device_id": browser_b},
        )
        assert claim.status_code == 200

        a_after = client.get(
            "/transactions",
            headers={**_bearer(user_a), "X-Device-Id": browser_a},
        )
        assert a_after.status_code == 401
        assert a_after.json()["detail"]["code"] == "DEVICE_DISPLACED"

        b_after = client.get(
            "/transactions",
            headers={**_bearer(user_a), "X-Device-Id": browser_b},
        )
        assert b_after.status_code == 200
    finally:
        client.post(
            "/auth/claim_device",
            headers=_bearer(user_a),
            json={"device_id": browser_a},
        )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _bearer(user) -> dict[str, str]:
    """Support bearer."""
    return {"Authorization": f"Bearer {user.jwt}"}
