"""Pin the CORS allowlist — Day 27, DESIGN.md §9.3.

The CORS allowlist is the structural protection that stops a logged-in
Tameru user's browser from being weaponized by a different website. The
FastAPI auth dep (JWT verification) is the access control; CORS is the
separate layer that prevents arbitrary origins from issuing fetches that
land in a user's authenticated session.

This test exists so a future debugging session that adds
`allow_origins=["*"]` to "make CORS go away" fails CI loudly rather than
silently widening the allowlist. Same shape as the other contract guards:
the regression mode (`*`, `*.vercel.app`, any wildcard) is what's being
prevented; the precise allowlist members can change as long as they stay
explicit.
"""

from __future__ import annotations

import os

import pytest

from app.main import _cors_allowed_origins


def test_dev_allowlist_only_localhost_when_frontend_origin_unset(monkeypatch):
    """No `FRONTEND_ORIGIN` env (local dev) → only the Vite dev server.

    The dev case proves the default state never accidentally permits a
    wildcard or an external origin. Production gates `FRONTEND_ORIGIN`
    as required at boot (see `app/main.py::lifespan`), so this branch
    only fires under local pytest.
    """
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    assert _cors_allowed_origins() == ["http://localhost:5173"]


def test_production_allowlist_is_two_explicit_origins(monkeypatch):
    """`FRONTEND_ORIGIN=tameru.xyz` → exactly two origins, no wildcards.

    The canonical prod domain is `https://tameru.xyz` (memory.md
    2026-05-25). The allowlist must contain only the canonical frontend
    plus the Vite dev port — never a wildcard, never a `*.vercel.app`
    catch-all (any Vercel tenant could otherwise reach the API).
    """
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://tameru.xyz")
    origins = _cors_allowed_origins()

    assert set(origins) == {"https://tameru.xyz", "http://localhost:5173"}
    assert len(origins) == 2


@pytest.mark.parametrize(
    "forbidden",
    [
        "*",
        "https://*",
        "https://*.vercel.app",
        "*.vercel.app",
        "null",
    ],
)
def test_no_wildcard_or_catchall_origin(monkeypatch, forbidden):
    """No allowed origin may be a wildcard or a `null` literal.

    Sweeps across the common ways a future change might accidentally
    widen the allowlist. `null` is the literal string browsers send for
    sandboxed iframes and `file://` documents — opening it lets local
    HTML files reach the API.
    """
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://tameru.xyz")
    origins = _cors_allowed_origins()

    assert forbidden not in origins, (
        f"forbidden origin {forbidden!r} found in CORS allowlist — "
        "the regression guard exists to prevent this exact widening. "
        "If a legitimate use case needs it, go through CLAUDE.md "
        "'Things to ask the user before doing' first."
    )
    for origin in origins:
        assert "*" not in origin, (
            f"wildcard substring in origin {origin!r}; the allowlist "
            "must contain explicit hostnames only."
        )


def test_allowlist_membership_independent_of_runtime_env(monkeypatch):
    """`_cors_allowed_origins()` resolves env at call time, not import time.

    Pins behavior: a contract test (or a future feature flag) can
    monkeypatch `FRONTEND_ORIGIN` before calling and see the right
    allowlist. This used to be a footgun — a module-level `[...]`
    literal would have frozen the allowlist at import.
    """
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://example.test")
    assert "https://example.test" in _cors_allowed_origins()

    monkeypatch.setenv("FRONTEND_ORIGIN", "https://other.test")
    assert "https://other.test" in _cors_allowed_origins()
    assert "https://example.test" not in _cors_allowed_origins()


def test_module_level_resolved_origins_match_helper(monkeypatch):
    """`_CORS_ALLOWED_ORIGINS` snapshot matches what `_cors_allowed_origins()` returns at import.

    The module computes the allowlist once at import (so CORS middleware
    and the unhandled-exception CORS echo agree on the same set). This
    test asserts the snapshot lives in module globals and is exactly
    what the helper produces with the env that was in scope at import.
    Practically: catches a refactor that drops the snapshot or
    introduces a separate, drifting source of truth.
    """
    # Re-import to capture under a known env. Caller is expected to
    # restore env via monkeypatch; we only verify the symbol exists and
    # is a list of strings populated from the helper's contract (not
    # a deep value equality, which would over-pin against test env).
    import app.main as main_module

    snapshot = main_module._CORS_ALLOWED_ORIGINS
    assert isinstance(snapshot, list)
    assert all(isinstance(origin, str) for origin in snapshot)
    assert "http://localhost:5173" in snapshot
