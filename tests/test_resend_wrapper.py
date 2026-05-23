"""Boundary-adapter contract tests for app/integrations/resend.py.

These run against a mocked `resend.Emails.send` so they're pure unit
tests — no network, no Supabase. They pin two contract properties that
Codex 2026-05-23 flagged:

1. The Idempotency-Key must reach Resend's SDK via the `options`
   argument (HTTP request header), NOT inside `params["headers"]`
   (which would attach it as a custom *email* header that Resend
   does not dedupe on).
2. A missing RESEND_API_KEY must surface as `ResendSendResult(success=False)`,
   NOT raise — otherwise the cron's "wrapper contract violation" path
   leaves a reserved email_log slot held with no email shipped.
"""

from __future__ import annotations

import pytest

from app.integrations import resend as resend_wrapper


def test_idempotency_key_goes_through_options_not_email_headers(monkeypatch):
    """Regression: Idempotency-Key belongs in SendOptions, not params.headers.

    The Resend Python SDK distinguishes:
      - `params["headers"]`  → custom MIME headers on the outgoing email.
      - `options["idempotency_key"]` → HTTP Idempotency-Key on the API call.

    The whole point of Layer 2 (24h dedupe by key) only fires for the
    second form. Putting it under params.headers would be a silent
    correctness regression — the key is set, tests pass, but a urllib3
    retry still creates two emails.
    """
    captured = {}

    def fake_send(params, options=None):
        """Capture both arguments so we can assert placement."""
        captured["params"] = params
        captured["options"] = options
        return {"id": "msg_test"}

    monkeypatch.setenv("RESEND_API_KEY", "re_test_dummy")
    monkeypatch.setattr(resend_wrapper.resend.Emails, "send", fake_send)

    result = resend_wrapper.send_digest_email(
        to="alice@example.com",
        subject="Tameru — week of May 11–17",
        html="<p>hi</p>",
        text="hi",
        list_unsubscribe_url="https://api.tameru.app/unsubscribe?u=1&token=t",
        list_unsubscribe_mailto="mailto:unsubscribe@mail.tameru.app",
        idempotency_key="digest:user-1:2026-05-11",
    )

    assert result.success is True
    assert result.message_id == "msg_test"

    # Options carries the idempotency key.
    assert captured["options"] is not None
    assert captured["options"].get("idempotency_key") == "digest:user-1:2026-05-11"

    # Params.headers must NOT carry it (that's the bug we're guarding against).
    headers = captured["params"].get("headers") or {}
    assert "Idempotency-Key" not in headers
    # Sanity: the email-header surface still carries the unsubscribe headers.
    assert "List-Unsubscribe" in headers
    assert "List-Unsubscribe-Post" in headers


def test_missing_resend_api_key_returns_failure_not_raises(monkeypatch):
    """Codex regression: an unset RESEND_API_KEY must return success=False.

    Older code called `_require_env("RESEND_API_KEY")` BEFORE the try
    block, so the RuntimeError escaped the wrapper. The cron's
    "wrapper contract violation" branch then held the reservation
    slot without sending — meaning a subsequent run with the key
    configured would skip the user until manual ops cleared the row.
    Returning success=False instead means the cron's "Resend rejected"
    branch fires, releasing the slot for a same-week retry.
    """
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    result = resend_wrapper.send_digest_email(
        to="alice@example.com",
        subject="x",
        html="<p>x</p>",
        text="x",
        list_unsubscribe_url="https://api.tameru.app/unsubscribe?u=1&token=t",
        list_unsubscribe_mailto="mailto:unsubscribe@mail.tameru.app",
        idempotency_key="digest:user-1:2026-05-11",
    )

    assert result.success is False
    # The exception class name (RuntimeError) is what _require_env raises.
    assert result.error_code == "RuntimeError"
    assert result.message_id is None


def test_sdk_exception_returns_failure_not_raises(monkeypatch):
    """The wrapper never propagates SDK exceptions to the caller.

    Documented contract: the cron's batch loop must continue past a
    single user's send failure. If `resend.Emails.send` raises for
    any reason (network, auth, payload), the wrapper converts it to
    a ResendSendResult(success=False) with the exception class name.
    """

    def boom(params, options=None):
        """Simulate any SDK-side exception."""
        raise ConnectionError("simulated TCP RST")

    monkeypatch.setenv("RESEND_API_KEY", "re_test_dummy")
    monkeypatch.setattr(resend_wrapper.resend.Emails, "send", boom)

    result = resend_wrapper.send_digest_email(
        to="alice@example.com",
        subject="x",
        html="<p>x</p>",
        text="x",
        list_unsubscribe_url="https://x",
        list_unsubscribe_mailto="mailto:x",
        idempotency_key="digest:user-1:2026-05-11",
    )

    assert result.success is False
    assert result.error_code == "ConnectionError"
