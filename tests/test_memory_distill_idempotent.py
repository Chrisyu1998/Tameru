"""Day 16 — distill_session called twice for the same conversation is a no-op.

The piggyback predicate in POST /chat/turn excludes any conversation
that has a conversation_distillation_state row — so the second call
shouldn't happen in normal operation. But defense in depth matters:
- A race where two chat turns from the same user fire the piggyback
  before either's BackgroundTask completes could schedule two
  distillations of the same conversation_id.
- A manual replay or admin re-run should also be safe.

Contract: a second distill_session call for an already-distilled
conversation makes no Anthropic call and writes no additional rows.
Mechanism: the implementation should fast-path on the existence of a
conversation_distillation_state row before reading chat_messages.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest


from app.agent import memory as memory_module  # noqa: E402
from app.db import supabase_for_user  # noqa: E402




pytestmark = pytest.mark.usefixtures("clean_memory")


@dataclass
class _Usage:
    """Minimal usage stand-in."""
    input_tokens: int = 200
    output_tokens: int = 40


@dataclass
class _MockMessage:
    """Minimal Message stand-in."""
    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self):
        """Default usage if omitted."""
        if self.usage is None:
            self.usage = _Usage()


class _CountingClient:
    """Counts messages.create() calls; returns a fixed scripted payload."""

    def __init__(self, payload):
        """Initialize with a JSON list to return on every create()."""
        self.call_count = 0
        outer = self
        text = json.dumps(payload)

        class _Messages:
            """Inner namespace counting create() calls."""
            def create(self, **kwargs):
                """Increment counter and return the scripted payload."""
                outer.call_count += 1
                return _MockMessage(content=[{"type": "text", "text": text}])

        self.messages = _Messages()


def test_second_distill_call_is_noop(user_a, monkeypatch):
    """First call distills; second call returns without touching Anthropic
    or writing more rows."""
    conversation_id = uuid.uuid4()
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user",      "I really want to hit my CSR Q2 SUB"),
            ("assistant", "How much have you spent so far?"),
            ("user",      "$2.1K"),
            ("assistant", "You're $1.9K from $4K. Plenty of runway."),
        ],
    )

    counting = _CountingClient(
        payload=[
            {
                "fact": "User is working toward CSR $4K SUB",
                "category": "goal",
                "relevance_score": 0.85,
            }
        ],
    )
    monkeypatch.setattr(memory_module, "_anthropic_client", lambda: counting)

    memory_module.distill_session(user_a.jwt, conversation_id)
    assert counting.call_count == 1, "first distillation didn't call Haiku"

    # Second call must be a fast-path no-op.
    memory_module.distill_session(user_a.jwt, conversation_id)
    assert counting.call_count == 1, (
        "second distill_session call hit Anthropic — fast-path on "
        "conversation_distillation_state is missing"
    )

    client = supabase_for_user(user_a.jwt)
    mem_rows = (
        client.table("user_memory")
        .select("id")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert len(mem_rows) == 1, (
        "second distill_session call produced duplicate user_memory rows"
    )


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
