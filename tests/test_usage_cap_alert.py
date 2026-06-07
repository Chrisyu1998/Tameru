"""Daily-cap Sentry alert — `_alert_daily_cap_reached`.

The cap gate (`tests/test_usage_cap.py`) proves a blocked turn raises
`UsageCapExceeded` before any Anthropic call. This file proves the
*alerting* side of that gate, in isolation from Supabase:

  1. Crossing the cap emits exactly one Sentry message, at `warning`
     level, fingerprinted by (user_id, UTC date) so Sentry collapses a
     user's repeated same-day blocked attempts into one issue.
  2. Staying under the cap emits nothing.
  3. A Sentry hiccup never propagates — the cap response must stay clean
     (the request is already failing closed).

Pure unit tests: `_today_chat_tokens_used` and `_daily_cap_tokens` are
monkeypatched so nothing touches Postgres, and the Sentry scope is faked.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent import middleware as mw
from app.agent.middleware import UsageCapExceeded, assert_within_usage_cap
from app.auth import AuthedUser


@pytest.fixture
def authed() -> AuthedUser:
    """A throwaway AuthedUser — no network identity needed for these tests."""
    return AuthedUser(jwt="jwt-not-used", user_id=uuid.uuid4(), email="cap@example.com")


def test_crossing_cap_emits_one_warning_alert(authed, monkeypatch):
    """At/over cap fires a single `warning` Sentry message fingerprinted
    by user + UTC date, carrying the used/cap token counts in extras."""
    monkeypatch.setattr(mw, "_daily_cap_tokens", lambda: 500)
    monkeypatch.setattr(mw, "_today_chat_tokens_used", lambda user: 600)
    scope = _FakeScope()
    captured: list[str] = []
    monkeypatch.setattr(mw.sentry_sdk, "new_scope", lambda: scope)
    monkeypatch.setattr(mw.sentry_sdk, "capture_message", captured.append)

    with pytest.raises(UsageCapExceeded):
        assert_within_usage_cap(authed)

    assert captured == ["Daily chat token cap reached"]
    assert scope.level == "warning"
    assert scope.fingerprint[0] == "chat-daily-cap"
    assert scope.fingerprint[1] == str(authed.user_id)
    assert scope.extras["used_tokens"] == 600
    assert scope.extras["cap_tokens"] == 500


def test_under_cap_emits_no_alert(authed, monkeypatch):
    """Below the cap the gate passes and Sentry is never touched."""
    monkeypatch.setattr(mw, "_daily_cap_tokens", lambda: 500)
    monkeypatch.setattr(mw, "_today_chat_tokens_used", lambda user: 100)
    captured: list[str] = []
    monkeypatch.setattr(mw.sentry_sdk, "capture_message", captured.append)

    assert_within_usage_cap(authed)  # no raise

    assert captured == []


def test_alert_failure_does_not_mask_the_cap(authed, monkeypatch):
    """If Sentry raises, the user must still get the cap error, not a 500
    — alerting is best-effort and swallowed (see the helper's docstring)."""
    monkeypatch.setattr(mw, "_daily_cap_tokens", lambda: 500)
    monkeypatch.setattr(mw, "_today_chat_tokens_used", lambda user: 999)

    def _boom():
        """Stand in for a Sentry SDK that raises on use."""
        raise RuntimeError("sentry exploded")

    monkeypatch.setattr(mw.sentry_sdk, "new_scope", _boom)

    with pytest.raises(UsageCapExceeded) as exc_info:
        assert_within_usage_cap(authed)
    assert exc_info.value.code == "DAILY_CAP_EXCEEDED"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


class _FakeScope:
    """Records what the helper sets on the Sentry scope.

    Doubles as the context manager `new_scope()` returns so the
    `with ... as scope:` block in `_alert_daily_cap_reached` sees it.
    """

    def __init__(self) -> None:
        """Start with nothing set."""
        self.level: str | None = None
        self.fingerprint: list | None = None
        self.extras: dict = {}

    def set_extra(self, key: str, value: object) -> None:
        """Mirror `sentry_sdk.Scope.set_extra`."""
        self.extras[key] = value

    def __enter__(self) -> "_FakeScope":
        """Enter the `with` block, handing back self as the scope."""
        return self

    def __exit__(self, *_exc) -> bool:
        """Never suppress exceptions from the block."""
        return False
