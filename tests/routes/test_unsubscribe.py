"""GET/POST /unsubscribe — one-click List-Unsubscribe contract.

The route flips users_meta.weekly_digest_enabled to false on a valid
HMAC token without requiring auth (the token IS the authorization).
Tests use the bootstrapped user_a fixture; the digest column exists
on every users_meta row by default (the column has DEFAULT true).
"""

from __future__ import annotations

import base64
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app
from app.util.unsubscribe import make_unsubscribe_token


@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI test client."""
    return TestClient(app)


def test_get_with_valid_token_flips_column(client, user_a):
    """A valid GET flips weekly_digest_enabled to false and shows success page."""
    user_id = uuid.UUID(user_a.id)
    token = make_unsubscribe_token(user_id, "digest")

    # Sanity: starts true (DEFAULT true).
    db = supabase_for_user(user_a.jwt)
    initial = (
        db.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    # Reset to true in case a prior test flipped it.
    if not initial or initial[0]["weekly_digest_enabled"] is False:
        db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
            "user_id", user_a.id
        ).execute()

    resp = client.get(
        "/unsubscribe",
        params={"user": str(user_id), "kind": "digest", "token": token},
    )
    assert resp.status_code == 200
    assert "You're unsubscribed" in resp.text
    assert resp.headers["content-type"].startswith("text/html")

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


def test_post_with_valid_token_returns_200(client, user_a):
    """RFC 8058 one-click POST returns 200 with empty body and flips the column."""
    user_id = uuid.UUID(user_a.id)
    token = make_unsubscribe_token(user_id, "digest")

    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()

    resp = client.post(
        "/unsubscribe",
        params={"user": str(user_id), "kind": "digest", "token": token},
    )
    assert resp.status_code == 200
    assert resp.text == ""

    after = (
        db.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert after["weekly_digest_enabled"] is False

    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()


def test_bad_token_returns_403_without_state_change(client, user_a):
    """Forged or tampered token returns 403; column stays as-is."""
    user_id = uuid.UUID(user_a.id)
    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()

    resp = client.get(
        "/unsubscribe",
        params={"user": str(user_id), "kind": "digest", "token": "forged"},
    )
    assert resp.status_code == 403

    after = (
        db.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    # State unchanged — the bad token didn't flip the column.
    assert after["weekly_digest_enabled"] is True


def test_unknown_kind_returns_400(client, user_a):
    """An unknown unsubscribe kind is rejected at the route boundary."""
    user_id = uuid.UUID(user_a.id)
    # Token doesn't matter — the kind validation fires first.
    resp = client.get(
        "/unsubscribe",
        params={"user": str(user_id), "kind": "newsletter", "token": "x"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_unsubscribe_secret(monkeypatch):
    """Seed a deterministic secret so the route's verifier matches the test."""
    secret = base64.b64encode(b"\xaa" * 32).decode("ascii")
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", secret)
