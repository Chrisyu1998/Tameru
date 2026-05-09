"""JWT-based auth for FastAPI handlers (CLAUDE.md invariant 1).

The backend never talks to Supabase Auth on the request hot path. Every
incoming request carries `Authorization: Bearer <jwt>` issued by Supabase
Auth; we verify it locally with the project's public JWKS and hand the
verified identity to downstream code as `AuthedUser`.

The JWT string itself flows into `app.db.supabase_for_user` so Postgres
sees `request.jwt.claims.sub` and enforces RLS — a handler that forgets
`WHERE user_id = ?` still cannot leak data.

Signing mode: Supabase issues ES256 JWTs (EC P-256 keys) signed by the
project's rotating signing keys and published at the JWKS URL below. We
pin `algorithms=["ES256"]` rather than accepting the wider `["ES256",
"RS256"]` so an attacker who somehow got a matching-kid RS256 key
published can't mount an algorithm-confusion attack. `PyJWKClient` caches
the JWKS and refreshes on a `kid` miss, so verification is in-process
with no per-request round trip.

Two dependency variants live here. `get_current_user_jwt` is the bare JWT
verifier — used on the small set of routes that intentionally run before
the device gate (the ones that establish or query device state). Every
other authenticated route uses `get_current_user_with_device`, which
additionally enforces single-active-device per user (DESIGN.md §9.1,
CLAUDE.md invariant 5) by comparing the request's `X-Device-Id` header
to `users_meta.active_device_id`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient

from app.db import supabase_for_user

_JWT_ALGORITHMS = ["ES256"]
_JWT_AUDIENCE = "authenticated"

_jwks_client: PyJWKClient | None = None
_jwt_issuer: str | None = None


def _jwks() -> tuple[PyJWKClient, str]:
    """Lazy-init the JWKS client + issuer string.

    Kept lazy so that `app.main` imports cleanly even when `SUPABASE_URL` is
    unset — unauthenticated routes (/healthz, /docs) stay available, and a
    call to a protected route surfaces the misconfiguration as a 500 on
    that route rather than a full application boot failure.
    """
    global _jwks_client, _jwt_issuer
    if _jwks_client is None:
        url = os.environ.get("SUPABASE_URL")
        if not url:
            raise RuntimeError("SUPABASE_URL is not set. See .env.example.")
        base = url.rstrip("/")
        _jwks_client = PyJWKClient(
            f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True
        )
        _jwt_issuer = f"{base}/auth/v1"
    return _jwks_client, _jwt_issuer


@dataclass(frozen=True)
class AuthedUser:
    jwt: str
    user_id: UUID
    email: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_jwt(request: Request) -> AuthedUser:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header or not header.lower().startswith("bearer "):
        raise _unauthorized("missing bearer token")
    token = header.split(" ", 1)[1].strip()
    if not token:
        raise _unauthorized("missing bearer token")

    jwks_client, issuer = _jwks()
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=_JWT_ALGORITHMS,
            audience=_JWT_AUDIENCE,
            issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"invalid token: {exc.__class__.__name__}") from exc

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise _unauthorized("token missing sub/email")

    return AuthedUser(jwt=token, user_id=UUID(sub), email=email)


def _device_displaced() -> HTTPException:
    """401 with the structured `DEVICE_DISPLACED` payload the frontend
    branches on to render the displacement modal (Day 7 prompt). The
    `MISSING_DEVICE_ID` branch is the same status code so a frontend that
    forgets to send the header surfaces the same modal — a missing header
    is, from the user's seat, indistinguishable from a stale session and
    the only safe action is to sign in again.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "DEVICE_DISPLACED", "message": "session ended on this device"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _missing_device() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "MISSING_DEVICE_ID", "message": "X-Device-Id header required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_with_device(
    request: Request,
    user: AuthedUser = Depends(get_current_user_jwt),
) -> AuthedUser:
    """JWT-verified user + single-active-device enforcement.

    Used on every authenticated route except the small set that legitimately
    runs before a device claim exists: `/me` (the frontend reads it to decide
    whether to bootstrap or claim) and the `/auth/*` device endpoints
    themselves. Putting the device check in those routes would deadlock the
    new-user flow — the very call meant to set `active_device_id` would 401
    because no device was claimed yet.

    Failure modes both return 401 with `code=DEVICE_DISPLACED` (or
    `MISSING_DEVICE_ID`). The frontend's `apiFetch` wrapper inspects the
    body's `code` and pops the displacement modal — see frontend/src/lib/api.ts.
    """
    device_id = request.headers.get("x-device-id")
    if not device_id:
        raise _missing_device()
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("users_meta")
        .select("active_device_id")
        .eq("user_id", str(user.user_id))
        .execute()
    )
    if not resp.data or resp.data[0].get("active_device_id") != device_id:
        raise _device_displaced()
    return user
