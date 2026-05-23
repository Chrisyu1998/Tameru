"""HMAC unsubscribe token round-trip + tampering rejection.

Pure unit test — no Supabase, no FastAPI. Sets the env var directly so
the module's `_require_secret()` resolves to a known value.
"""

from __future__ import annotations

import base64
import os
from uuid import UUID, uuid4

import pytest

from app.util.unsubscribe import make_unsubscribe_token, verify_unsubscribe_token


def test_round_trip_verifies():
    """A freshly minted token verifies against the same (user_id, kind)."""
    user_id = uuid4()
    token = make_unsubscribe_token(user_id, "digest")
    assert verify_unsubscribe_token(token, user_id, "digest") is True


def test_wrong_user_id_rejected():
    """A token for one user must not verify for another."""
    user_a = uuid4()
    user_b = uuid4()
    token = make_unsubscribe_token(user_a, "digest")
    assert verify_unsubscribe_token(token, user_b, "digest") is False


def test_tampered_token_rejected():
    """Flipping a single character in the token breaks verification."""
    user_id = uuid4()
    token = make_unsubscribe_token(user_id, "digest")
    # Flip the last char (preserve length).
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert verify_unsubscribe_token(tampered, user_id, "digest") is False


def test_rotated_secret_invalidates_old_tokens(monkeypatch):
    """Rotating DIGEST_UNSUBSCRIBE_SECRET breaks every previously minted token.

    Documented property of the no-expiry design (memory.md / DESIGN.md
    §6.4): we accept that "revoke all" == "rotate the secret."
    """
    user_id = uuid4()
    old_token = make_unsubscribe_token(user_id, "digest")
    new_secret = base64.b64encode(b"\x01" * 32).decode("ascii")
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", new_secret)
    assert verify_unsubscribe_token(old_token, user_id, "digest") is False


def test_invalid_base64_secret_fails_loud(monkeypatch):
    """A non-base64 secret raises at first use rather than producing wrong tokens."""
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", "not===valid===base64!!!")
    with pytest.raises(RuntimeError, match="not valid base64"):
        make_unsubscribe_token(uuid4(), "digest")


def test_missing_secret_fails_loud(monkeypatch):
    """Unset secret raises rather than silently using an empty key."""
    monkeypatch.delenv("DIGEST_UNSUBSCRIBE_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="is not set"):
        make_unsubscribe_token(uuid4(), "digest")


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_unsubscribe_secret(monkeypatch):
    """Seed a deterministic secret so tokens are reproducible within a test."""
    secret = base64.b64encode(b"\x00" * 32).decode("ascii")
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", secret)
