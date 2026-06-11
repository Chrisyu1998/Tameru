"""Cron eligibility + idempotency + batch-resilience tests.

The cron orchestrates eligibility + compose + send + log; the
compose/send pieces have their own dedicated tests. Here we
monkeypatch those external calls and assert the orchestration
correctly:

  - includes opted-in users with recent activity
  - excludes opted-out users
  - excludes zombie users (no tx in past 4 weeks)
  - is idempotent: a re-run on the same week produces no new sends
  - survives a single user's error and continues the batch
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.cron import digest as cron_digest
from app.db import supabase_admin, supabase_for_user
from app.integrations.resend import ResendSendResult
from app.services.digest import (
    CategoryRollup,
    DigestPayload,
    SonnetCallLog,
)

# Failure-path prefs restore + seeded-ledger-row teardown for the shared user_a (audit P3-37/P3-38).
pytestmark = pytest.mark.usefixtures("preserve_user_a_meta", "cleanup_user_a_ledger")


ET = ZoneInfo("America/New_York")


@pytest.fixture
def stub_compose_and_send(monkeypatch):
    """Replace compose_digest + send_digest_email with deterministic stubs.

    Records every call so tests can assert what got sent and to whom.
    """
    sent: list[str] = []

    def fake_compose(client, user_id, *, anthropic_client=None):
        """Return a canned payload + call log without hitting Supabase or Anthropic."""
        payload = DigestPayload(
            user_id=user_id,
            week_start=datetime(2026, 5, 11, tzinfo=ET),
            week_end=datetime(2026, 5, 17, 23, 59, 59, tzinfo=ET),
            week_total=Decimal("100.00"),
            baseline_avg=Decimal("80.00"),
            top_category=CategoryRollup(
                category="Dining",
                week_total=Decimal("50.00"),
                baseline_avg=Decimal("40.00"),
            ),
            home_currency="USD",
            observation="Spending steady.",
            nudge=None,
        )
        call_log = SonnetCallLog(
            input_tokens=100,
            output_tokens=20,
            latency_ms=300,
            success=True,
            error_code=None,
        )
        return payload, call_log

    def fake_send(**kwargs):
        """Record the recipient address and return a fake success result."""
        sent.append(kwargs["to"])
        return ResendSendResult(
            message_id=f"msg_{len(sent):04d}",
            success=True,
            error_code=None,
        )

    monkeypatch.setattr(cron_digest, "compose_digest", fake_compose)
    monkeypatch.setattr(cron_digest, "send_digest_email", fake_send)
    return sent


def test_eligible_user_receives_send(user_a, stub_compose_and_send):
    """User with recent tx + opt-in → one send + one email_log row."""
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)

    from uuid import UUID

    report = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert report.sent == 1
    assert report.failed == 0
    assert user_a.email in stub_compose_and_send

    admin = supabase_admin()
    logs = (
        admin.table("email_log")
        .select("success, kind, provider_message_id")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert any(r["success"] and r["kind"] == "digest" for r in logs)

    _clear_email_log(user_a.id)


def test_opted_out_user_skipped(user_a, stub_compose_and_send):
    """weekly_digest_enabled=false → eligible=0, no compose/send fired."""
    _seed_recent_tx(user_a)
    db = supabase_for_user(user_a.jwt)
    db.table("users_meta").update({"weekly_digest_enabled": False}).eq(
        "user_id", user_a.id
    ).execute()
    _clear_email_log(user_a.id)

    from uuid import UUID

    report = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert report.sent == 0
    assert report.eligible == 0
    assert stub_compose_and_send == []

    _reset_meta(user_a)


def test_idempotent_rerun_zero_new_sends(user_a, stub_compose_and_send):
    """Two runs the same week → second run produces zero new successful rows."""
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)

    from uuid import UUID

    cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    # Capture state after run 1.
    admin = supabase_admin()
    sends_before = len(stub_compose_and_send)
    logs_before = len(
        admin.table("email_log")
        .select("id")
        .eq("user_id", user_a.id)
        .eq("success", True)
        .execute()
        .data
    )

    # Run 2 — same week.
    report = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert report.sent == 0, "second run should not send again"
    assert report.skipped_already_sent == 1
    assert len(stub_compose_and_send) == sends_before, "send shouldn't fire again"

    logs_after = len(
        admin.table("email_log")
        .select("id")
        .eq("user_id", user_a.id)
        .eq("success", True)
        .execute()
        .data
    )
    assert logs_after == logs_before, "no new success rows"

    _clear_email_log(user_a.id)


def test_send_exception_after_reserve_does_not_duplicate_next_run(user_a, monkeypatch):
    """Crash-mid-send scenario: next cron run does NOT re-send.

    Regression guard for the Codex P2 finding. Reserves first, then
    `send_digest_email` raises (simulating a worker crash after the
    reservation row landed). A second run on the same week must not
    issue a fresh send — the reservation row (still success=true with
    no provider_message_id) holds the partial unique slot.
    """
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)

    from uuid import UUID

    def fake_compose(client, user_id, *, anthropic_client=None):
        """Return a minimal payload (no Anthropic call)."""
        payload = DigestPayload(
            user_id=user_id,
            week_start=datetime(2026, 5, 11, tzinfo=ET),
            week_end=datetime(2026, 5, 17, 23, 59, 59, tzinfo=ET),
            week_total=Decimal("0.00"),
            baseline_avg=Decimal("0.00"),
            top_category=None,
            home_currency="USD",
            observation="ok",
            nudge=None,
        )
        return payload, SonnetCallLog(
            input_tokens=1, output_tokens=1, latency_ms=1, success=True, error_code=None
        )

    sends: list[str] = []

    def first_run_crashing_send(**kwargs):
        """Raise to simulate a worker crash post-reservation."""
        sends.append(kwargs["to"])
        # We deliberately do NOT release the reservation — that's the
        # crash semantics. The slot stays held.
        raise RuntimeError("simulated worker crash mid-send")

    monkeypatch.setattr(cron_digest, "compose_digest", fake_compose)
    monkeypatch.setattr(cron_digest, "send_digest_email", first_run_crashing_send)

    # Run 1: crashes mid-send. The except handler will try to release
    # the reservation, which DOES fire here because the exception is
    # caught by the outer try. So this test actually exercises the
    # release path. To simulate a *true* mid-flight crash (where the
    # release never runs), patch _release_reservation to no-op so the
    # row stays success=true with no message_id.
    monkeypatch.setattr(cron_digest, "_release_reservation", lambda *a, **kw: None)

    cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))
    assert len(sends) == 1, "first run attempted send"

    # Now: the reservation row exists with success=true, no
    # provider_message_id. Run a second cron with a fresh send stub —
    # it must skip this user because the slot is held.
    def must_not_be_called(**kwargs):
        """If reached, the test fails — we expect skipped_already_sent."""
        sends.append(kwargs["to"])
        from app.integrations.resend import ResendSendResult
        return ResendSendResult(message_id="msg_should_not_exist", success=True, error_code=None)

    monkeypatch.setattr(cron_digest, "send_digest_email", must_not_be_called)

    report = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert report.sent == 0, "second run must NOT send — slot is held"
    assert report.skipped_already_sent == 1
    assert len(sends) == 1, "send_digest_email must not have been called again"

    _clear_email_log(user_a.id)


def test_send_failure_releases_slot_for_same_week_retry(user_a, monkeypatch, stub_compose_and_send):
    """A failed send flips success=false and the slot is released.

    The partial unique index is `WHERE success`. Flipping the row from
    success=true to success=false removes it from the index, so a
    follow-up run in the same week can reserve again and try Resend
    once more. This is the "retry on transient failure" property.
    """
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)
    from uuid import UUID

    # First run: send fails. compose is stubbed by the fixture; replace
    # the send to return failure (no exception, just success=false).
    def failing_send(**kwargs):
        """Return a failed-send result."""
        from app.integrations.resend import ResendSendResult
        return ResendSendResult(message_id=None, success=False, error_code="SmtpError")

    monkeypatch.setattr(cron_digest, "send_digest_email", failing_send)
    report1 = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))
    assert report1.failed == 1

    # Reservation row should now be success=false.
    admin = supabase_admin()
    rows = (
        admin.table("email_log")
        .select("success, error_code")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert any(r["success"] is False and r["error_code"] == "SmtpError" for r in rows)

    # Second run: replace send with a success stub. Because the failed
    # row is success=false, it's not in the partial unique index, so
    # reservation can succeed again.
    succeeded: list[str] = []

    def succeeding_send(**kwargs):
        """Return a success-send result."""
        from app.integrations.resend import ResendSendResult
        succeeded.append(kwargs["to"])
        return ResendSendResult(message_id="msg_retry_ok", success=True, error_code=None)

    monkeypatch.setattr(cron_digest, "send_digest_email", succeeding_send)
    report2 = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))
    assert report2.sent == 1, "released slot must be retryable in the same week"
    assert succeeded == [user_a.email]

    _clear_email_log(user_a.id)


def test_post_send_failure_does_not_release_slot(user_a, monkeypatch, stub_compose_and_send):
    """Codex 2026-05-23 P2 regression: a failure AFTER Resend accepts must NOT release.

    The earlier (broken) code had one broad `except` around the whole
    iteration that called `_release_reservation` on any exception —
    including ones thrown by `_finalize_reservation_success` or the
    `ai_call_log` insert, both of which run after Resend has already
    accepted the message. Releasing in those cases lets the next cron
    run reserve and send a duplicate. This test forces a post-send
    exception and asserts the next run sees the slot as held.
    """
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)
    from uuid import UUID

    # First run: send succeeds (per fixture), but _finalize_reservation_success
    # throws. The reservation row should stay success=true with no msg_id —
    # NOT flip to success=false (which would release the slot).
    def boom_finalize(*args, **kwargs):
        """Simulate a transient DB error on the post-send UPDATE."""
        raise RuntimeError("simulated finalize failure after Resend accepted")

    monkeypatch.setattr(cron_digest, "_finalize_reservation_success", boom_finalize)

    report = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))
    assert report.sent == 1, (
        "report.sent must count post-send-failures: Resend accepted the message"
    )

    # The reservation row exists with success=true (held). msg_id may be null.
    admin = supabase_admin()
    rows = (
        admin.table("email_log")
        .select("success")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert any(r["success"] is True for r in rows), (
        "post-send failure must keep success=true so the partial unique "
        "index continues to hold the slot"
    )

    # Second run with a clean finalize stub: must SKIP this user, not send again.
    monkeypatch.setattr(
        cron_digest, "_finalize_reservation_success", lambda *a, **kw: None
    )
    sends_before = list(stub_compose_and_send)
    report2 = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert report2.sent == 0, "second run must NOT re-send after post-send failure"
    assert report2.skipped_already_sent == 1
    assert stub_compose_and_send == sends_before, "no fresh Resend call should fire"

    _clear_email_log(user_a.id)


def test_idempotency_key_is_passed_to_resend(user_a, monkeypatch):
    """Layer 2 regression: send_digest_email must receive an Idempotency-Key.

    Without the key, the SDK's urllib3 retries on transient TCP errors
    can produce duplicate sends that the DB lock can't see. The key is
    deterministic per (user, week) so a retry of the same logical send
    dedups at Resend's side.
    """
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)
    from uuid import UUID

    captured_kwargs: dict = {}

    def fake_compose(client, user_id, *, anthropic_client=None):
        """Return a payload with a known week_start so the key is predictable."""
        payload = DigestPayload(
            user_id=user_id,
            week_start=datetime(2026, 5, 11, tzinfo=ET),
            week_end=datetime(2026, 5, 17, 23, 59, 59, tzinfo=ET),
            week_total=Decimal("100.00"),
            baseline_avg=Decimal("80.00"),
            top_category=None,
            home_currency="USD",
            observation="steady",
            nudge=None,
        )
        return payload, SonnetCallLog(
            input_tokens=1, output_tokens=1, latency_ms=1, success=True, error_code=None
        )

    def capturing_send(**kwargs):
        """Capture all kwargs the cron passes to send_digest_email."""
        captured_kwargs.update(kwargs)
        from app.integrations.resend import ResendSendResult
        return ResendSendResult(message_id="msg_idem_test", success=True, error_code=None)

    monkeypatch.setattr(cron_digest, "compose_digest", fake_compose)
    monkeypatch.setattr(cron_digest, "send_digest_email", capturing_send)

    cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert "idempotency_key" in captured_kwargs, (
        "send_digest_email must receive idempotency_key — required by the wrapper signature"
    )
    expected_key = f"digest:{user_a.id}:2026-05-11"
    assert captured_kwargs["idempotency_key"] == expected_key, (
        f"key must be deterministic per (user, week); got {captured_kwargs['idempotency_key']!r}"
    )

    _clear_email_log(user_a.id)


def test_send_failure_does_not_halt_batch(user_a, monkeypatch):
    """A Resend exception writes success=false and the batch continues."""
    _reset_meta(user_a)
    _seed_recent_tx(user_a)
    _clear_email_log(user_a.id)

    from uuid import UUID

    def fake_compose(client, user_id, *, anthropic_client=None):
        """Return a minimal canned payload (no Supabase/Anthropic calls)."""
        payload = DigestPayload(
            user_id=user_id,
            week_start=datetime(2026, 5, 11, tzinfo=ET),
            week_end=datetime(2026, 5, 17, 23, 59, 59, tzinfo=ET),
            week_total=Decimal("0.00"),
            baseline_avg=Decimal("0.00"),
            top_category=None,
            home_currency="USD",
            observation="ok",
            nudge=None,
        )
        return payload, SonnetCallLog(
            input_tokens=1, output_tokens=1, latency_ms=1, success=True, error_code=None
        )

    def boom_send(**kwargs):
        """Return a failed-send result to exercise the batch-error path."""
        return ResendSendResult(message_id=None, success=False, error_code="SmtpError")

    monkeypatch.setattr(cron_digest, "compose_digest", fake_compose)
    monkeypatch.setattr(cron_digest, "send_digest_email", boom_send)

    report = cron_digest.send_weekly_digests(only_user_id=UUID(user_a.id))

    assert report.failed == 1
    assert report.sent == 0

    admin = supabase_admin()
    logs = (
        admin.table("email_log")
        .select("success, error_code")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert any(r["success"] is False and r["error_code"] == "SmtpError" for r in logs)

    _clear_email_log(user_a.id)


# ---------------------------------------------------------------------------
# Day 26b — CTA URL builder (unit tests, no DB).
# ---------------------------------------------------------------------------


def test_app_cta_url_clean_origin(monkeypatch):
    """`_app_cta_url` builds `{origin}/?source=digest` from a clean FRONTEND_ORIGIN."""
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://tameru.xyz")
    assert cron_digest._app_cta_url() == "https://tameru.xyz/?source=digest"


def test_app_cta_url_strips_trailing_slash(monkeypatch):
    """Trailing-slash FRONTEND_ORIGIN must NOT produce a double-slash URL.

    Load-bearing per memory.md 2026-05-26 — a value like
    `https://tameru.xyz/` would otherwise emit `https://tameru.xyz//?source=digest`
    which still resolves but breaks URL-equality assertions and reads
    confusingly in any log line.
    """
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://tameru.xyz/")
    assert cron_digest._app_cta_url() == "https://tameru.xyz/?source=digest"


def test_app_cta_url_raises_when_unset(monkeypatch):
    """Missing FRONTEND_ORIGIN fails loud — Day 26b's cron-side fail-loud."""
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    with pytest.raises(RuntimeError, match="FRONTEND_ORIGIN"):
        cron_digest._app_cta_url()


# ---------------------------------------------------------------------------
# Fixtures + helpers.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _required_env(monkeypatch):
    """Stub the env vars the cron's URL building reads."""
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", base64.b64encode(b"\x10" * 32).decode())
    # The unsubscribe URL must point at the FastAPI backend (a Railway
    # host in prod), not the Vercel frontend — /unsubscribe is a FastAPI
    # route. Tests use a stub origin.
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://api-test.local")
    # Day 26b — the CTA URL points at the Vercel PWA host. Tests use a
    # stub origin; the `_app_cta_url` unit tests below verify the
    # trailing-slash normalization and the fail-loud behavior directly.
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://app-test.local")


def _seed_recent_tx(user) -> None:
    """Ensure the user has at least one active transaction in the past 4 weeks."""
    client = supabase_for_user(user.jwt)
    today = datetime.now(timezone.utc).date().isoformat()
    client.table("transactions").insert(
        {
            "user_id": user.id,
            "merchant": "test merchant",
            "amount": "50.00",
            "date": today,
            "category": "Dining",
            "source": "nlp",
        }
    ).execute()


def _clear_email_log(user_id: str) -> None:
    """Wipe email_log rows for a user (service role)."""
    admin = supabase_admin()
    admin.table("email_log").delete().eq("user_id", user_id).execute()


def _reset_meta(user) -> None:
    """Restore weekly_digest_enabled=true for the user (RLS owner-UPDATE)."""
    db = supabase_for_user(user.jwt)
    db.table("users_meta").update({"weekly_digest_enabled": True}).eq(
        "user_id", user.id
    ).execute()
