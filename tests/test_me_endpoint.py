"""Smoke tests for GET /me — acceptance criteria from Day 3.

Covers:
- Valid JWT returns 200 with the claims straight from the token.
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
    return TestClient(app)


def test_me_returns_claims(client, user_a):
    resp = client.get("/me", headers={"Authorization": f"Bearer {user_a.jwt}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"user_id": user_a.id, "email": user_a.email}


def test_me_without_header_returns_401(client):
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_with_bare_bearer_returns_401(client):
    resp = client.get("/me", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_me_with_tampered_signature_returns_401(client, user_a):
    head, payload, sig = user_a.jwt.split(".")
    flipped_char = "A" if sig[-1] != "A" else "B"
    tampered = ".".join([head, payload, sig[:-1] + flipped_char])

    resp = client.get("/me", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
