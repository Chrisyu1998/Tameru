"""T3 (2026-07-03) — the CURRENT conversation is distilled as it grows.

Before T3, distillation only fired for a *prior* conversation the user
returned to after the 10-minute idle window (find_idle_undistilled_
conversation, which structurally excludes the current conversation). A
user who tested in one sitting never triggered it — the "0 facts"
complaint. T3 adds:

  * a `message_count` column on conversation_distillation_state,
  * `find_conversation_to_distill(conv, min, delta)` — a current-turn
    probe the chat route calls alongside the idle backstop,
  * count-based re-distillation in distill_session (re-run once the
    conversation grows by REDISTILL_DELTA messages).

Coverage here:
  1. A grown conversation re-distills (unit, on distill_session).
  2. Growth below REDISTILL_DELTA does NOT re-distill (unit).
  3. POST /chat/turn on a continuing conversation schedules distillation
    of the current conversation (route).

Real local Supabase (the message_count column + RPC + upsert are the
production contract); Anthropic is mocked.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import memory as memory_module  # noqa: E402
from app.db import supabase_for_user  # noqa: E402
from app.main import app  # noqa: E402


pytestmark = pytest.mark.usefixtures("clean_memory")


@dataclass
class _Usage:
    """Minimal usage stand-in."""

    input_tokens: int = 200
    output_tokens: int = 40


@dataclass
class _MockMessage:
    """Minimal Message stand-in with a single text block."""

    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default usage if omitted."""
        if self.usage is None:
            self.usage = _Usage()


class _CountingClient:
    """Counts messages.create() calls; returns a fixed scripted payload."""

    def __init__(self, payload: list[dict[str, Any]]):
        """Initialize with a JSON list to return on every create()."""
        self.call_count = 0
        outer = self
        text = json.dumps(payload)

        class _Messages:
            """Inner namespace counting create() calls."""

            def create(self, **kwargs: Any) -> _MockMessage:
                """Increment counter and return the scripted payload."""
                outer.call_count += 1
                return _MockMessage(content=[{"type": "text", "text": text}])

        self.messages = _Messages()


def test_grown_conversation_redistills(user_a, monkeypatch):
    """A conversation distilled at 4 messages re-distills once it grows to
    8 (delta == REDISTILL_DELTA), and the state row advances to 8."""
    conversation_id = uuid.uuid4()
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user", "I put my Costco runs on CSR for the points"),
            ("assistant", "Good call — 2x on CSR at wholesale clubs."),
            ("user", "trying to hit the $4K SUB by Q2"),
            ("assistant", "You're well on track."),
        ],
    )

    counting = _CountingClient(
        payload=[
            {
                "fact": "User puts Costco purchases on CSR",
                "category": "card_preference",
                "relevance_score": 0.7,
            }
        ],
    )
    monkeypatch.setattr(memory_module, "_anthropic_client", lambda: counting)

    memory_module.distill_session(user_a.jwt, conversation_id)
    assert counting.call_count == 1

    client = supabase_for_user(user_a.jwt)
    state = (
        client.table("conversation_distillation_state")
        .select("message_count")
        .eq("conversation_id", str(conversation_id))
        .execute()
        .data
    )
    assert state[0]["message_count"] == 4, "first distill didn't record the count"

    # Grow the conversation by REDISTILL_DELTA (4) more messages.
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user", "also I'm planning a Tokyo trip in April"),
            ("assistant", "Fun — want me to watch that spend?"),
            ("user", "yeah, and I usually eat out ~3x a week"),
            ("assistant", "Noted."),
        ],
    )

    memory_module.distill_session(user_a.jwt, conversation_id)
    assert counting.call_count == 2, (
        "grown conversation (8 msgs vs distilled-through 4) did not re-distill"
    )

    state = (
        client.table("conversation_distillation_state")
        .select("message_count")
        .eq("conversation_id", str(conversation_id))
        .execute()
        .data
    )
    assert state[0]["message_count"] == 8, "re-distill didn't advance message_count"


def test_small_growth_does_not_redistill(user_a, monkeypatch):
    """Growth below REDISTILL_DELTA is not enough to re-run Haiku."""
    conversation_id = uuid.uuid4()
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user", "I prefer earning on groceries over dining"),
            ("assistant", "Got it."),
            ("user", "what's my grocery total?"),
            ("assistant", "$412 this month."),
        ],
    )

    counting = _CountingClient(
        payload=[
            {
                "fact": "User prefers earning on groceries over dining",
                "category": "preference",
                "relevance_score": 0.6,
            }
        ],
    )
    monkeypatch.setattr(memory_module, "_anthropic_client", lambda: counting)

    memory_module.distill_session(user_a.jwt, conversation_id)
    assert counting.call_count == 1

    # Only 2 more messages (delta 2 < REDISTILL_DELTA 4).
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user", "thanks"),
            ("assistant", "anything else?"),
        ],
    )

    memory_module.distill_session(user_a.jwt, conversation_id)
    assert counting.call_count == 1, (
        "conversation grew by only 2 (< REDISTILL_DELTA) but re-distilled anyway"
    )


def test_current_conversation_triggers_distillation(user_a, monkeypatch):
    """POST /chat/turn on a continuing, never-distilled conversation with
    >= MIN_CONVERSATION_MESSAGES committed rows schedules distillation of
    THAT conversation — no return-visit, no 10-minute idle wait."""
    conv = uuid.uuid4()
    # Recent (default created_at = now()) so the idle backstop would ignore
    # it anyway; and it IS the current conversation, which the idle probe
    # excludes by construction. Only the current-conversation probe fires.
    _seed_conversation(
        user_a, conv,
        turns=[
            ("user", "I'm saving for a wedding next fall"),
            ("assistant", "Congrats! Want me to flag big one-offs?"),
            ("user", "yes please, and I put everything on my Amex"),
            ("assistant", "Noted."),
        ],
    )

    captured: list[tuple[str, uuid.UUID]] = []
    _install_chat_stubs(monkeypatch, captured)

    client = TestClient(app)
    client.post(
        "/chat/turn",
        headers={
            "Authorization": f"Bearer {user_a.jwt}",
            "X-Device-Id": user_a.device_id or "test-device",
        },
        json={"conversation_id": str(conv), "message": "how am I doing?"},
    )

    assert len(captured) == 1, (
        f"expected the current conversation to be scheduled once, got {captured}"
    )
    _, conv_arg = captured[0]
    assert conv_arg == conv, (
        f"current-conversation probe scheduled the wrong conversation: "
        f"got {conv_arg}, expected {conv}"
    )


def test_idle_backstop_redistills_grown_abandoned_conversation(user_a, monkeypatch):
    """Codex-review fix #1: the idle backstop is now delta-aware, so a
    conversation that was distilled once, grew, and was then abandoned (>10
    min idle) gets re-scheduled — instead of being excluded forever by the
    old `NOT EXISTS (state row)` anti-join."""
    grown_idle = uuid.uuid4()
    eleven_min_ago = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=11)
    ).isoformat()
    # 8 committed messages, all idle.
    _seed_dated_conversation(
        user_a, grown_idle,
        turns=[(r, t, eleven_min_ago) for r, t in [
            ("user", "I put groceries on my Amex"),
            ("assistant", "Noted."),
            ("user", "and I'm saving for a house"),
            ("assistant", "Got it."),
            ("user", "also planning a trip to Kyoto"),
            ("assistant", "Fun."),
            ("user", "I eat out about twice a week"),
            ("assistant", "Noted."),
        ]],
    )
    # Distilled through only the first 4 messages, then it grew to 8 (delta 4).
    client = supabase_for_user(user_a.jwt)
    client.table("conversation_distillation_state").insert(
        {
            "conversation_id": str(grown_idle),
            "user_id": user_a.id,
            "message_count": 4,
        }
    ).execute()

    captured: list[tuple[str, uuid.UUID]] = []
    _install_chat_stubs(monkeypatch, captured)

    # Fire a turn in a DIFFERENT (fresh) conversation.
    fresh = uuid.uuid4()
    tc = TestClient(app)
    tc.post(
        "/chat/turn",
        headers={
            "Authorization": f"Bearer {user_a.jwt}",
            "X-Device-Id": user_a.device_id or "test-device",
        },
        json={"conversation_id": str(fresh), "message": "hi"},
    )

    assert len(captured) == 1, (
        f"idle backstop did not re-schedule the grown, abandoned conversation "
        f"(it was distilled once and should re-distill after +4 msgs): {captured}"
    )
    assert captured[0][1] == grown_idle


def test_state_upsert_is_monotonic(user_a):
    """Codex-review fix #2: `upsert_conversation_distillation_state` uses
    GREATEST, so a straggler task writing a smaller message_count cannot
    regress the stored value (which would over-trigger later re-distills)."""
    client = supabase_for_user(user_a.jwt)
    conv = uuid.uuid4()

    def _stored() -> int:
        """Read back the stored message_count for the conversation."""
        return (
            client.table("conversation_distillation_state")
            .select("message_count")
            .eq("conversation_id", str(conv))
            .execute()
            .data[0]["message_count"]
        )

    client.rpc(
        "upsert_conversation_distillation_state",
        {"p_conversation_id": str(conv), "p_message_count": 8},
    ).execute()
    assert _stored() == 8

    # A straggler writing a smaller count must NOT lower the stored value.
    client.rpc(
        "upsert_conversation_distillation_state",
        {"p_conversation_id": str(conv), "p_message_count": 5},
    ).execute()
    assert _stored() == 8, "message_count regressed — GREATEST guard missing"

    # A larger count advances it.
    client.rpc(
        "upsert_conversation_distillation_state",
        {"p_conversation_id": str(conv), "p_message_count": 12},
    ).execute()
    assert _stored() == 12


# ---------------------------------------------------------------------------
# Test helpers.
# ---------------------------------------------------------------------------


def _install_chat_stubs(monkeypatch, captured):
    """Replace `distill_session` with a capturing stand-in and `stream_turn`
    with a no-tool single-done iterator so the chat route completes without
    a live Anthropic client. Mirrors test_memory_piggyback's helper."""

    def _capture(user_jwt, conversation_id):
        """Stand-in distill_session that records the call shape."""
        captured.append((user_jwt, conversation_id))

    from app.agent import memory as _memory_module
    monkeypatch.setattr(_memory_module, "distill_session", _capture)
    from app.routes import chat as chat_route
    if hasattr(chat_route, "distill_session"):
        monkeypatch.setattr(chat_route, "distill_session", _capture)

    from app.agent.loop import StreamEvent

    def _stub_stream(user, history, message):
        """Yield a token + done frame; mirrors the loop's StreamEvent shape."""
        yield StreamEvent(kind="token", text="ok")
        yield StreamEvent(
            kind="done",
            done={
                "tool_calls": [],
                "content_blocks": [{"type": "text", "text": "ok"}],
                "turn_messages": [
                    {"role": "user", "content": message},
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                    },
                ],
            },
        )

    monkeypatch.setattr(chat_route, "stream_turn", _stub_stream)


def _seed_conversation(user, conversation_id, *, turns):
    """Insert N chat_messages rows under one conversation_id."""
    client = supabase_for_user(user.jwt)
    rows = [
        {
            "user_id": user.id,
            "conversation_id": str(conversation_id),
            "role": role,
            "content_blocks": [{"type": "text", "text": text}],
        }
        for role, text in turns
    ]
    client.table("chat_messages").insert(rows).execute()


def _seed_dated_conversation(user, conversation_id, *, turns):
    """Insert chat_messages rows with explicit created_at (for idle tests).

    `turns` is a list of (role, text, created_at_iso) so a conversation can be
    aged past the idle backstop's 10-minute threshold.
    """
    client = supabase_for_user(user.jwt)
    rows = [
        {
            "user_id": user.id,
            "conversation_id": str(conversation_id),
            "role": role,
            "content_blocks": [{"type": "text", "text": text}],
            "created_at": when,
        }
        for role, text, when in turns
    ]
    client.table("chat_messages").insert(rows).execute()
