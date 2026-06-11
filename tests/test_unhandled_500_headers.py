"""The unhandled-500 must stay legible cross-origin and carry its headers.

Guards the fix for the documented "Load failed" incident (memory.md
2026-05-20): an unhandled exception is synthesized into a 500 by
Starlette's ServerErrorMiddleware, which sits OUTSIDE CORSMiddleware,
SecurityHeadersMiddleware, and CorrelationIdMiddleware — so without
`_unhandled_exception_handler` stamping them itself, the response ships
with no Access-Control-Allow-Origin (browser blocks it, PWA shows an
opaque network error), no hardening headers, and no X-Request-ID. The
audit (P3-18) found zero assertions on any of this; the incident fix was
unguarded.

The unhandled exception is injected via a dependency override on
/me's auth dependency — no throwaway route is added to the prod app.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import get_current_user_jwt
from app.main import app


def test_unhandled_500_carries_cors_and_hardening_headers():
    """An unhandled RuntimeError → 500 with CORS + hardening headers.

    `raise_server_exceptions=False` lets the TestClient return the
    synthesized 500 instead of re-raising into the test. The Origin must
    be one the allowlist contains (localhost:5173 is always allowed) for
    the CORS re-attach branch to fire.
    """

    def _boom():
        """Stand-in unhandled failure inside the request path."""
        raise RuntimeError("contract: deliberately unhandled")

    app.dependency_overrides[get_current_user_jwt] = _boom
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me", headers={"Origin": "http://localhost:5173"})
    finally:
        app.dependency_overrides.pop(get_current_user_jwt, None)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "internal_error"
    # CORS re-attach — the original "Load failed" fix.
    assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert resp.headers["Vary"] == "Origin"
    # Hardening headers + request id — stamped by the handler because
    # ServerErrorMiddleware bypasses every user middleware (audit P3-24).
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers.get("X-Request-ID"), "correlation id must survive onto the 500"


def test_unhandled_500_omits_cors_for_unlisted_origin():
    """A disallowed Origin gets the 500 but no allow-origin header.

    The re-attach must not become a wildcard — echoing arbitrary origins
    would relax CORS on exactly the error path.
    """

    def _boom():
        """Stand-in unhandled failure inside the request path."""
        raise RuntimeError("contract: deliberately unhandled")

    app.dependency_overrides[get_current_user_jwt] = _boom
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me", headers={"Origin": "https://evil.example"})
    finally:
        app.dependency_overrides.pop(get_current_user_jwt, None)

    assert resp.status_code == 500
    assert "Access-Control-Allow-Origin" not in resp.headers
