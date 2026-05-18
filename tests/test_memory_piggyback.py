"""Day 16 — POST /chat/turn schedules distillation for idle, undistilled prior conversations.

The piggyback predicate is the *only* trigger for distillation in v1
(no client timer, no beforeunload, no pg_cron). Two cases to cover:

  - Stale (latest message > 10 min old, not yet distilled): a
    BackgroundTask must be scheduled with that conversation_id.
  - Fresh (latest message < 10 min old): no task scheduled.

The implementation lives at the top of POST /chat/turn in
app/routes/chat.py. We stub out `stream_turn` so the chat handler
returns a clean SSE response — this test cares about the piggyback
side effect, not the loop. BackgroundTasks fire after the streaming
response completes; FastAPI's TestClient drains the stream
synchronously, so the captured list is populated by the time the
.post() call returns.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user  # noqa: E402
from app.main import app  # noqa: E402


pytestmark = pytest.mark.usefixtures("clean_memory")


def test_stale_conversation_triggers_piggyback(user_a, monkeypatch):
    """An undistilled conversation with last message > 10 min ago →
    distill_session is scheduled with that conversation_id when the
    user fires a turn in a different conversation."""
    stale_conv = uuid.uuid4()
    # Seed an 11-minute-old chat_messages pair under stale_conv.
    eleven_min_ago = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=11)
    ).isoformat()
    _seed_chat_messages(
        user_a, stale_conv,
        turns=[
            ("user",      "I'm trying to hit my CSR Q2 SUB", eleven_min_ago),
            ("assistant", "Got it — how close are you?",     eleven_min_ago),
            ("user",      "About 2.1k of 4k",                eleven_min_ago),
            ("assistant", "Plenty of runway.",               eleven_min_ago),
        ],
    )

    captured: list[tuple[str, uuid.UUID]] = []
    _install_chat_stubs(monkeypatch, captured)

    fresh_conv = uuid.uuid4()
    client = TestClient(app)
    resp = client.post(
        "/chat/turn",
        headers={
            "Authorization": f"Bearer {user_a.jwt}",
            "X-Device-Id": user_a.device_id or "test-device",
        },
        json={"conversation_id": str(fresh_conv), "message": "hi"},
    )
    # The chat route 200s as soon as the stream opens; per-frame errors
    # would surface in the body. Either way, what we actually care about
    # is the BackgroundTask side effect captured below.
    assert resp.status_code in (200, 422, 500), (
        f"unexpected status {resp.status_code}; piggyback check should not "
        f"affect handler status: body={resp.text[:200]}"
    )

    assert len(captured) == 1, (
        f"expected 1 piggyback distill_session call, got {len(captured)}: "
        f"{captured}"
    )
    _, conv_arg = captured[0]
    assert conv_arg == stale_conv, (
        f"piggyback scheduled wrong conversation: got {conv_arg}, "
        f"expected {stale_conv}"
    )


def test_fresh_conversation_does_not_trigger_piggyback(user_a, monkeypatch):
    """Latest message 9 min old → not stale per the SQL predicate → no
    BackgroundTask scheduled."""
    fresh_prior = uuid.uuid4()
    nine_min_ago = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=9)
    ).isoformat()
    _seed_chat_messages(
        user_a, fresh_prior,
        turns=[
            ("user",      "what's my dining total", nine_min_ago),
            ("assistant", "$312",                   nine_min_ago),
            ("user",      "ok",                     nine_min_ago),
            ("assistant", "anything else?",         nine_min_ago),
        ],
    )

    captured: list[tuple[str, uuid.UUID]] = []
    _install_chat_stubs(monkeypatch, captured)

    new_conv = uuid.uuid4()
    client = TestClient(app)
    client.post(
        "/chat/turn",
        headers={
            "Authorization": f"Bearer {user_a.jwt}",
            "X-Device-Id": user_a.device_id or "test-device",
        },
        json={"conversation_id": str(new_conv), "message": "hi"},
    )

    assert captured == [], (
        f"piggyback fired on a sub-10-minute-old conversation: {captured}"
    )


def test_short_stale_conversation_does_not_starve_longer_one(user_a, monkeypatch):
    """Two undistilled, >10-min-stale conversations exist for the user:
    a 2-message one (too short to distill) and a 4-message one. The
    RPC must skip the short one and return the long one — otherwise the
    short conversation gets selected every turn (Python short-circuits
    without writing the state row) and starves the eligible long one
    forever. This is the regression test for the Codex finding on
    `find_idle_undistilled_conversation`.
    """
    eleven_min_ago = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=11)
    ).isoformat()
    twelve_min_ago = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=12)
    ).isoformat()

    # Older, longer conversation = the eligible one. Even though it's
    # older than the short one, the SQL ORDER BY MAX(created_at) DESC
    # would normally prefer the short one (newer). With the COUNT >= 4
    # gate, the short one falls out of the candidate set entirely.
    long_stale = uuid.uuid4()
    _seed_chat_messages(
        user_a, long_stale,
        turns=[
            ("user",      "long convo 1", twelve_min_ago),
            ("assistant", "ok",           twelve_min_ago),
            ("user",      "long convo 2", twelve_min_ago),
            ("assistant", "ok",           twelve_min_ago),
        ],
    )

    short_stale = uuid.uuid4()
    _seed_chat_messages(
        user_a, short_stale,
        turns=[
            ("user",      "hi",      eleven_min_ago),
            ("assistant", "hello",   eleven_min_ago),
        ],
    )

    captured: list[tuple[str, uuid.UUID]] = []
    _install_chat_stubs(monkeypatch, captured)

    new_conv = uuid.uuid4()
    client = TestClient(app)
    client.post(
        "/chat/turn",
        headers={
            "Authorization": f"Bearer {user_a.jwt}",
            "X-Device-Id": user_a.device_id or "test-device",
        },
        json={"conversation_id": str(new_conv), "message": "hi"},
    )

    assert len(captured) == 1, (
        f"expected exactly one piggyback call, got {len(captured)}: {captured}"
    )
    _, conv_arg = captured[0]
    assert conv_arg == long_stale, (
        f"piggyback selected the SHORT stale conversation, which would "
        f"starve the eligible long one. got {conv_arg}, expected {long_stale}"
    )


# ---------------------------------------------------------------------------
# Test helpers.
# ---------------------------------------------------------------------------


def _install_chat_stubs(monkeypatch, captured):
    """Replace `distill_session` with a capturing stand-in and `stream_turn`
    with a no-tool, single-done-event iterator so the chat route does not
    need a live Anthropic client to complete the response."""

    def _capture(user_jwt, conversation_id):
        """Stand-in distill_session that records the call shape."""
        captured.append((user_jwt, conversation_id))

    # Patch the function inside the source module so any binding that
    # imported it by name picks up the stub.
    from app.agent import memory as memory_module
    monkeypatch.setattr(memory_module, "distill_session", _capture)
    from app.routes import chat as chat_route
    if hasattr(chat_route, "distill_session"):
        monkeypatch.setattr(chat_route, "distill_session", _capture)

    # Stub stream_turn to a single-shot iterator that yields a token and
    # a done event. This bypasses any need for ANTHROPIC_API_KEY and
    # avoids invoking the agent loop.
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
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        )

    monkeypatch.setattr(chat_route, "stream_turn", _stub_stream)


def _seed_chat_messages(user, conversation_id, *, turns):
    """Insert chat_messages rows under one conversation_id with explicit
    created_at timestamps."""
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
