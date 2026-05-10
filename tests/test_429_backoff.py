"""Day 9a — Anthropic 429 retry behavior, end-to-end through `run_turn`.

The retry logic lives in `app/agent/loop.py` (inlined into the iteration
body so each attempt writes its own `ai_call_log` row — Day 8's load-
bearing invariant that the 429 retry path must not collapse). These
tests exercise the property that matters externally:

  * Single 429 → success on retry: turn completes, two audit rows
    (one failure, one success).
  * Two consecutive 429s: `ProviderRateLimited` raised, two audit rows
    (both failure, both `error_code='RateLimitError'`).
  * Non-429 exception (e.g. `BadRequestError`): propagates immediately
    with no retry, one audit row with the original error_code.

The row-count assertions are the structural guard that a future
refactor doesn't reintroduce a `with_429_backoff`-style wrapper that
collapses attempts into a single row.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from app.agent import loop as loop_module
from app.agent.loop import ProviderRateLimited, run_turn
from app.auth import AuthedUser
from app.db import supabase_for_user


def _rate_limit_error() -> anthropic.RateLimitError:
    """Construct a real RateLimitError instance — its constructor wants
    a status code + a body, not just a message."""
    return anthropic.RateLimitError(
        message="rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
        body={"error": {"type": "rate_limit_error"}},
    )


def _bad_request_error() -> anthropic.BadRequestError:
    return anthropic.BadRequestError(
        message="bad",
        response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
        body={"error": {"type": "invalid_request_error"}},
    )


def _today_chat_rows(user) -> list[dict]:
    """Read today's chat_turn rows for a user, oldest first.

    Order matters for the row-count tests — the first attempt's row
    must come before the retry's row."""
    midnight = _dt.datetime.combine(
        _dt.datetime.now(_dt.timezone.utc).date(),
        _dt.time.min,
        tzinfo=_dt.timezone.utc,
    )
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("ai_call_log")
        .select("success, error_code, timestamp")
        .eq("user_id", user.id)
        .eq("task_type", "chat_turn")
        .gte("timestamp", midnight.isoformat())
        .order("timestamp", desc=False)
        .execute()
    )
    return resp.data or []


@pytest.fixture
def authed(user_a, admin_client) -> AuthedUser:
    """Wraps user_a; wipes today's chat_turn rows before each test so
    the per-test row-count assertions have a deterministic baseline.
    ai_call_log has no DELETE policy for end users (audit history is
    unscrubable per invariant 14), so the cleanup uses admin_client
    — same pattern as test_usage_cap.py."""
    midnight = _dt.datetime.combine(
        _dt.datetime.now(_dt.timezone.utc).date(),
        _dt.time.min,
        tzinfo=_dt.timezone.utc,
    )
    admin_client.table("ai_call_log").delete().eq("user_id", user_a.id).eq(
        "task_type", "chat_turn"
    ).gte("timestamp", midnight.isoformat()).execute()
    return AuthedUser(jwt=user_a.jwt, user_id=uuid.UUID(user_a.id), email=user_a.email)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The loop's retry path sleeps 2s; tests don't need the wall-clock
    pause. Patch on loop_module (where time.sleep is now called, since
    `with_429_backoff` was removed from middleware)."""
    monkeypatch.setattr(loop_module.time, "sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _anthropic_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(loop_module, "_client", None)


# ---------------------------------------------------------------------------
# Single 429 → retry succeeds.
# ---------------------------------------------------------------------------


def test_run_turn_recovers_from_single_429_and_logs_both_attempts(
    authed, user_a, monkeypatch
):
    """First call 429s, second succeeds. run_turn returns the
    successful response AND ai_call_log records both attempts —
    one failed, one succeeded — preserving the Day 8 invariant
    (one audit row per messages.create call) on the retry path."""
    from tests.test_agent_loop import _MockMessage, _text

    happy_response = _MockMessage(content=[_text("Recovered.")], stop_reason="end_turn")
    client = MagicMock()
    client.messages.create.side_effect = [
        _rate_limit_error(),
        happy_response,
    ]
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: client)

    turn = run_turn(authed, [], "first call will 429")

    assert turn.assistant_text == "Recovered."
    assert client.messages.create.call_count == 2

    rows = _today_chat_rows(user_a)
    assert len(rows) == 2, (
        f"expected one ai_call_log row per attempt (Day 8 invariant); got {len(rows)} "
        f"— a regression that re-collapses 429 retries into one row would fail here"
    )
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "RateLimitError"
    assert rows[1]["success"] is True
    assert rows[1]["error_code"] is None


# ---------------------------------------------------------------------------
# Two consecutive 429s → ProviderRateLimited, both attempts logged.
# ---------------------------------------------------------------------------


def test_run_turn_surfaces_provider_rate_limited_after_two_429s(
    authed, user_a, monkeypatch
):
    client = MagicMock()
    client.messages.create.side_effect = [
        _rate_limit_error(),
        _rate_limit_error(),
    ]
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: client)

    with pytest.raises(ProviderRateLimited) as exc_info:
        run_turn(authed, [], "always 429")

    assert client.messages.create.call_count == 2
    assert exc_info.value.code == "AI_PROVIDER_RATE_LIMITED"
    # The terminal exception chains back to the second RateLimitError;
    # the audit log already captured both attempts before the raise.
    assert isinstance(exc_info.value.__cause__, anthropic.RateLimitError)

    rows = _today_chat_rows(user_a)
    assert len(rows) == 2
    for row in rows:
        assert row["success"] is False
        # Both rows carry the underlying provider error type, not the
        # synthetic ProviderRateLimited wrapper — which is raised AFTER
        # the second row is written.
        assert row["error_code"] == "RateLimitError"


# ---------------------------------------------------------------------------
# Non-429 → no retry, propagates with its own error_code.
# ---------------------------------------------------------------------------


def test_run_turn_does_not_retry_non_429(authed, user_a, monkeypatch):
    """`BadRequestError` (e.g. malformed request) is a bug, not an
    outage. The loop must not retry on anything other than
    RateLimitError — silent retries on real bugs hide root causes."""
    client = MagicMock()
    client.messages.create.side_effect = [_bad_request_error()]
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: client)

    with pytest.raises(anthropic.BadRequestError):
        run_turn(authed, [], "this is malformed")

    assert client.messages.create.call_count == 1  # exactly one attempt

    rows = _today_chat_rows(user_a)
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "BadRequestError"
