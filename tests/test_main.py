"""`app/main.py` contract — top-level routes (/me today, /healthz later).

GET /me — acceptance criteria from Day 3, extended for Day 7's
`home_currency` field:
- Valid JWT returns 200 with the claims plus home_currency from users_meta.
- Pre-bootstrap user returns home_currency: null (Splash dispatches on this).
- No bearer header returns 401.
- Tampered signature returns 401.
- Bearer header without an actual token returns 401.

The signature-tampering test is what proves local verification is really
running: if we were passing the token through unchecked, a flipped-bit
signature would still authenticate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provide client."""
    return TestClient(app)


def test_me_returns_claims(client, user_a):
    # Day 7 extends /me to surface `home_currency` from users_meta. The
    # session-scoped user_a fixture pre-bootstraps with USD, so we expect
    # it on the response. /me itself stays outside the device gate, so no
    # X-Device-Id header is required here.
    """Verify that me returns claims."""
    resp = client.get("/me", headers={"Authorization": f"Bearer {user_a.jwt}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "user_id": user_a.id,
        "email": user_a.email,
        "home_currency": "USD",
    }


def test_me_returns_null_currency_before_bootstrap(client, user_unbootstrapped):
    # Pre-bootstrap user has no users_meta row. /me must still succeed —
    # the frontend keys off `home_currency: null` to route to the currency
    # picker. If /me 4xx'd here, the dispatch would be impossible.
    """Verify that me returns null currency before bootstrap."""
    resp = client.get(
        "/me", headers={"Authorization": f"Bearer {user_unbootstrapped.jwt}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == user_unbootstrapped.id
    assert body["email"] == user_unbootstrapped.email
    assert body["home_currency"] is None


def test_me_without_header_returns_401(client):
    """Verify that me without header returns 401."""
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_with_bare_bearer_returns_401(client):
    """Verify that me with bare bearer returns 401."""
    resp = client.get("/me", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_me_with_tampered_signature_returns_401(client, user_a):
    # Flip a middle base64 character of the signature, not the last one.
    # ES256 produces a 64-byte signature → 86 base64url chars. Because
    # 64 % 3 == 1, the final base64 char only encodes 4 meaningful bits
    # plus 4 padding bits; flipping within that padding zone can leave
    # the decoded signature bytes unchanged and the token still valid.
    # A mid-string flip always lands on 6 meaningful bits, so it always
    # corrupts the signature deterministically.
    """Verify that me with tampered signature returns 401."""
    head, payload, sig = user_a.jwt.split(".")
    mid = len(sig) // 2
    flipped_char = "A" if sig[mid] != "A" else "B"
    tampered = ".".join([head, payload, sig[:mid] + flipped_char + sig[mid + 1:]])

    resp = client.get("/me", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
