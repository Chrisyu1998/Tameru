"""HMAC-signed unsubscribe tokens for one-click email opt-out (DESIGN.md §6.4).

Pattern parallels `IMPORT_TOKEN_SECRET` for CSV import (memory.md
2026-05-19): stateless, server-secret-signed, no database lookup
required to verify. A token is `base64url(HMAC-SHA256(secret, payload))`
where `payload = f"{user_id}|{kind}"`.

NO EXPIRY by design. A user who finds a year-old digest in their archive
and clicks "unsubscribe" should still be unsubscribed. The only invalidation
path is rotating `DIGEST_UNSUBSCRIBE_SECRET` (which would invalidate
EVERY live unsubscribe link — the correct property if the secret leaks).

Verification uses `hmac.compare_digest` for constant-time comparison so
a timing-attack adversary cannot enumerate the signature byte by byte.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Literal
from uuid import UUID

UnsubscribeKind = Literal["digest"]


def make_unsubscribe_token(user_id: UUID, kind: UnsubscribeKind) -> str:
    """Mint an HMAC-SHA256 unsubscribe token for `(user_id, kind)`.

    Returns a base64url string (no padding). The caller embeds this in
    the unsubscribe URL's `token` query param alongside `user` and
    `kind`. The token is stateless — no DB row, no expiry, no
    revocation list.
    """
    secret = _require_secret()
    payload = _payload(user_id, kind)
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_unsubscribe_token(token: str, user_id: UUID, kind: UnsubscribeKind) -> bool:
    """Constant-time verify a token against `(user_id, kind)`.

    Returns True iff the token was minted by `make_unsubscribe_token`
    against the same `(user_id, kind)` with the current secret. A
    rotated secret invalidates all prior tokens — caller surfaces a
    403 the same as a forged token.
    """
    expected = make_unsubscribe_token(user_id, kind)
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _payload(user_id: UUID, kind: UnsubscribeKind) -> bytes:
    """Canonicalize `(user_id, kind)` to the bytes the HMAC signs."""
    return f"{user_id}|{kind}".encode("utf-8")


def _require_secret() -> bytes:
    """Read `DIGEST_UNSUBSCRIBE_SECRET` as bytes; fail loudly if unset.

    The env var is base64-encoded random bytes (32 bytes recommended)
    set in Railway. Decoding here means a misconfigured value (not
    valid base64) surfaces immediately rather than producing valid-
    looking-but-wrong tokens.
    """
    raw = os.environ.get("DIGEST_UNSUBSCRIBE_SECRET")
    if not raw:
        raise RuntimeError("DIGEST_UNSUBSCRIBE_SECRET is not set.")
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise RuntimeError(
            "DIGEST_UNSUBSCRIBE_SECRET is not valid base64."
        ) from exc
