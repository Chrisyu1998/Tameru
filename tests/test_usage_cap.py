"""Day 9a — per-user daily token cap middleware.

Three properties matter and have separate tests:

  1. Over-cap blocks the next turn *before* any Anthropic call fires.
  2. Yesterday's tokens (UTC) don't count — the cap resets at midnight.
  3. Gemini categorization tokens don't count against the chat cap —
     only `task_type='chat_turn'` rows do. This is load-bearing for
     Day 9b: propose_transaction will call Gemini for category
     suggestions, and those tokens must not silently eat the user's
     chat allowance.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest

from app.agent import loop as loop_module
from app.agent.loop import run_turn
from app.agent.middleware import UsageCapExceeded
from app.auth import AuthedUser
from app.db import supabase_for_user


@pytest.fixture
def authed(user_a, admin_client) -> AuthedUser:
    """Wraps the session-scoped user_a, but wipes today's ai_call_log
    rows before yielding so each cap test starts at a known
    accumulated-tokens count of zero. ai_call_log has no DELETE
    policy for end users (audit history is unscrubable per
    invariant 14), so the cleanup uses admin_client — the same
    pattern conftest uses for user teardown."""
    today = _dt.datetime.now(_dt.timezone.utc).date()
    midnight = _dt.datetime.combine(today, _dt.time.min, tzinfo=_dt.timezone.utc)
    admin_client.table("ai_call_log").delete().eq("user_id", user_a.id).gte(
        "timestamp", midnight.isoformat()
    ).execute()
    return AuthedUser(jwt=user_a.jwt, user_id=uuid.UUID(user_a.id), email=user_a.email)


def test_over_cap_raises_before_any_anthropic_call(authed, user_a):
    """Verify that over cap raises before any anthropic call."""
    now = _dt.datetime.now(_dt.timezone.utc)
    _seed_ai_call_log_row(
        user_a,
        task_type="chat_turn",
        input_tokens=400,
        output_tokens=200,  # 600 total, > 500 cap
        timestamp=now,
    )
    with pytest.raises(UsageCapExceeded) as exc_info:
        run_turn(authed, [], "this should never reach Anthropic")
    assert exc_info.value.code == "DAILY_CAP_EXCEEDED"
    assert exc_info.value.used >= 600
    assert exc_info.value.cap == 500


def test_yesterday_tokens_do_not_count(authed, user_a, monkeypatch):
    """A row dated before today's UTC midnight is outside the window —
    the user's cap must reset cleanly at midnight UTC even if yesterday
    was spent at-cap."""
    # Use a fresh _Boom that DOES return a real response so the turn
    # actually runs once the cap check passes.
    from tests.test_agent_loop import _MockMessage, _ScriptedClient, _text

    scripted = _ScriptedClient([
        _MockMessage(content=[_text("All clear.")], stop_reason="end_turn"),
    ])
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: scripted)

    yesterday = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
    _seed_ai_call_log_row(
        user_a,
        task_type="chat_turn",
        input_tokens=1000,
        output_tokens=1000,  # 2000 total — would block today
        timestamp=yesterday,
    )
    turn = run_turn(authed, [], "ok now?")
    assert turn.assistant_text == "All clear."
    assert scripted.call_count == 1


def test_categorization_tokens_do_not_count_against_chat_cap(authed, user_a, monkeypatch):
    """Day 9b's propose_transaction will call Gemini for category
    suggestions. Those tokens are logged with `task_type='categorization'`
    and must not push the user toward their chat cap — chat and
    categorization have different cost profiles and the cap covers only
    runaway-chat risk."""
    from tests.test_agent_loop import _MockMessage, _ScriptedClient, _text

    scripted = _ScriptedClient([
        _MockMessage(content=[_text("Still fine.")], stop_reason="end_turn"),
    ])
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: scripted)

    now = _dt.datetime.now(_dt.timezone.utc)
    # 10x the cap, but all categorization — must not block.
    _seed_ai_call_log_row(
        user_a,
        task_type="categorization",
        input_tokens=3000,
        output_tokens=2000,
        timestamp=now,
        provider="google",
        model="gemini-2.5-flash",
    )
    turn = run_turn(authed, [], "even with 5k gemini tokens today?")
    assert turn.assistant_text == "Still fine."


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _seed_ai_call_log_row(
    user,
    *,
    task_type: str,
    input_tokens: int,
    output_tokens: int,
    timestamp: _dt.datetime,
    provider: str = "anthropic",
    model: str = "claude-haiku-4-5",
) -> None:
    """Direct insert into ai_call_log via the user's JWT.

    The table's narrow INSERT policy (WITH CHECK user_id = auth.uid()) is
    what this test exercises end-to-end — and incidentally what the
    middleware's SELECT path relies on.
    """
    client = supabase_for_user(user.jwt)
    client.table("ai_call_log").insert({
        "user_id": user.id,
        "provider": provider,
        "model": model,
        "task_type": task_type,
        "prompt_version": "chat_v2",
        "prompt_hash": "x" * 64,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": 1,
        "success": True,
        "timestamp": timestamp.isoformat(),
    }).execute()

@pytest.fixture(autouse=True)
def _low_cap(monkeypatch):
    """Drop the cap to 500 tokens so a few hundred seeded tokens push us
    over. The default 200K would force seeding ~1700 turns per test."""
    monkeypatch.setenv("CHAT_USAGE_CAP_TOKENS_PER_DAY", "500")

@pytest.fixture(autouse=True)
def _no_anthropic_calls(monkeypatch):
    """If the cap check passes when it shouldn't, this fixture turns the
    silent failure (an accidental network call) into a loud one."""
    class _Boom:
        """Represent Boom."""
        class messages:
            """Represent messages."""

            @staticmethod
            def create(**_):
                """Provide create."""
                raise AssertionError(
                    "agent loop made an Anthropic call despite the cap check"
                )

    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: _Boom())
