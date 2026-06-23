"""POST /webhooks/resend — Svix signature verification + suppression.

Webhook handler is service-role-backed; it has no user JWT and the
inbound request is signed by Resend's Svix headers. We use the `svix`
library to mint valid signatures for the tests and assert that:

  - a valid email.bounced(hard) flips weekly_digest_enabled to false
    and stamps bounce_type='hard' on the matching email_log row;
  - a valid email.complained does the same with bounce_type='complaint';
  - a valid email.bounced(soft) does NOT suppress;
  - an invalid signature returns 400;
  - an unknown message_id returns 200 with no state change.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_admin, supabase_for_user
from app.main import app

# Failure-path cleanup: restore user_a's shared users_meta prefs even when asserts fail (audit P3-37).
pytestmark = pytest.mark.usefixtures("preserve_user_a_meta")



@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def webhook_secret(monkeypatch):
    """Mint a fresh Svix signing secret per test and expose to the route."""
    raw = secrets.token_bytes(24)
    # Svix secrets are usually base64'd with a `whsec_` prefix; the
    # library accepts either. Strip-prefix form is what the dashboard
    # gives you.
    secret = "whsec_" + raw.hex()
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", secret)
    return secret


def test_hard_bounce_suppresses(client, user_a, webhook_secret):
    """email.bounced with type=hard sets bounce_type and flips column."""
    admin = supabase_admin()
    # Seed: a successful email_log row with a known message id.
    message_id = f"msg_{uuid.uuid4().hex}"
    admin.table("email_log").insert(
        {
            "user_id": user_a.id,
            "kind": "digest",
            "success": True,
            "provider_message_id": message_id,
        }
    ).execute()
    # Ensure weekly_digest_enabled starts true.
    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()

    body = {
        "type": "email.bounced",
        "data": {
            "email_id": message_id,
            "bounce": {"type": "hard"},
        },
    }
    resp = _signed_request(client, webhook_secret, body)
    assert resp.status_code == 200

    # users_meta.weekly_digest_enabled flipped.
    meta = (
        admin.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert meta["weekly_digest_enabled"] is False

    # email_log.bounce_type stamped.
    log = (
        admin.table("email_log")
        .select("bounce_type")
        .eq("provider_message_id", message_id)
        .execute()
        .data[0]
    )
    assert log["bounce_type"] == "hard"

    # Cleanup.
    admin.table("email_log").delete().eq("provider_message_id", message_id).execute()
    admin.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()


def test_complaint_suppresses(client, user_a, webhook_secret):
    """email.complained always suppresses (no type discrimination needed)."""
    admin = supabase_admin()
    message_id = f"msg_{uuid.uuid4().hex}"
    admin.table("email_log").insert(
        {
            "user_id": user_a.id,
            "kind": "digest",
            "success": True,
            "provider_message_id": message_id,
        }
    ).execute()
    admin.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()

    body = {"type": "email.complained", "data": {"email_id": message_id}}
    resp = _signed_request(client, webhook_secret, body)
    assert resp.status_code == 200

    meta = (
        admin.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert meta["weekly_digest_enabled"] is False

    log = (
        admin.table("email_log")
        .select("bounce_type")
        .eq("provider_message_id", message_id)
        .execute()
        .data[0]
    )
    assert log["bounce_type"] == "complaint"

    admin.table("email_log").delete().eq("provider_message_id", message_id).execute()
    admin.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()


def test_soft_bounce_does_not_suppress(client, user_a, webhook_secret):
    """email.bounced with type=soft is a no-op — Resend retries internally."""
    admin = supabase_admin()
    message_id = f"msg_{uuid.uuid4().hex}"
    admin.table("email_log").insert(
        {
            "user_id": user_a.id,
            "kind": "digest",
            "success": True,
            "provider_message_id": message_id,
        }
    ).execute()
    admin.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user_a.id
    ).execute()

    body = {
        "type": "email.bounced",
        "data": {
            "email_id": message_id,
            "bounce": {"type": "soft"},
        },
    }
    resp = _signed_request(client, webhook_secret, body)
    assert resp.status_code == 200

    meta = (
        admin.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", user_a.id)
        .execute()
        .data[0]
    )
    assert meta["weekly_digest_enabled"] is True
    log = (
        admin.table("email_log")
        .select("bounce_type")
        .eq("provider_message_id", message_id)
        .execute()
        .data[0]
    )
    assert log["bounce_type"] is None

    admin.table("email_log").delete().eq("provider_message_id", message_id).execute()


def test_invalid_signature_returns_400(client, webhook_secret):
    """A tampered or missing Svix signature returns 400."""
    body = {"type": "email.bounced", "data": {}}
    resp = client.post(
        "/webhooks/resend",
        content=json.dumps(body).encode("utf-8"),
        headers={
            "webhook-id": "msg_x",
            "webhook-timestamp": "0",
            "webhook-signature": "v1,definitely-wrong",
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 400


def test_unknown_message_id_returns_200(client, webhook_secret):
    """A webhook for an unknown message_id is a 200 no-op (no state change)."""
    body = {
        "type": "email.bounced",
        "data": {
            "email_id": "msg_nonexistent",
            "bounce": {"type": "hard"},
        },
    }
    resp = _signed_request(client, webhook_secret, body)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _signed_request(client: TestClient, secret: str, body: dict):
    """POST the webhook with a valid Svix signature.

    Uses standardwebhooks (svix's upstream package) which exposes a
    public sign() method. The route imports the same library via
    svix.webhooks; the signature format is identical.

    sign() expects `data` as a str — the internal `f"{msg_id}.{ts}.{data}"`
    would otherwise embed the bytes repr, which fails verification.
    """
    from datetime import datetime, timezone

    from standardwebhooks.webhooks import Webhook as StdWebhook

    raw_str = json.dumps(body)
    raw_bytes = raw_str.encode("utf-8")
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    timestamp = datetime.now(timezone.utc)
    wh = StdWebhook(secret)
    signature = wh.sign(msg_id, timestamp, raw_str)
    headers = {
        "webhook-id": msg_id,
        "webhook-timestamp": str(int(timestamp.timestamp())),
        "webhook-signature": signature,
        "content-type": "application/json",
    }
    return client.post("/webhooks/resend", content=raw_bytes, headers=headers)
