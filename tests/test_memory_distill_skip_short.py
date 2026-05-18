"""Day 16 — distillation short-circuits on conversations with < 4 messages.

A 3-message conversation has no meaningful cross-session signal — the
LLM call would burn tokens to extract noise, and the
conversation_distillation_state row would lock the conversation out of
future distillation even if it grows past the threshold.

Contract: distill_session on a 3-row conversation must
  - not call Anthropic,
  - not write any user_memory row,
  - not write a conversation_distillation_state row (so a follow-up
    that grows the conversation can trigger distillation later).
"""

from __future__ import annotations

import uuid

import pytest


from app.agent import memory as memory_module  # noqa: E402
from app.db import supabase_for_user  # noqa: E402




pytestmark = pytest.mark.usefixtures("clean_memory")


class _ExplodingClient:
    """Any messages.create() call is a test failure."""

    class _Messages:
        """Inner namespace; .create() raises."""
        def create(self, **kwargs):
            """Raise so the test fails loudly if distill_session gates wrong."""
            raise AssertionError(
                "distill_session called Anthropic on a short conversation — "
                "the < 4 message short-circuit is missing"
            )

    messages = _Messages()


def test_short_conversation_skips_distillation(user_a, monkeypatch):
    """Three messages = no Haiku call, no DB writes anywhere."""
    conversation_id = uuid.uuid4()
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user",      "what's my dining total"),
            ("assistant", "$312 this month."),
            ("user",      "thanks"),
        ],
    )
    monkeypatch.setattr(memory_module, "_anthropic_client", lambda: _ExplodingClient())

    memory_module.distill_session(user_a.jwt, conversation_id)

    client = supabase_for_user(user_a.jwt)
    mem_rows = (
        client.table("user_memory")
        .select("id")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert mem_rows == [], "user_memory rows written for a short conversation"

    state_rows = (
        client.table("conversation_distillation_state")
        .select("conversation_id")
        .eq("conversation_id", str(conversation_id))
        .execute()
        .data
    )
    assert state_rows == [], (
        "conversation_distillation_state row written — locks out future "
        "distillation if the conversation grows"
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
