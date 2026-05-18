"""Day 16 — distill_session extracts atomic facts from a chat conversation.

End-to-end: seed a 6-message chat_messages conversation that mentions a
spending goal and a card preference; mock the Anthropic client so we
control the JSON response; invoke distill_session; assert two rows in
user_memory with the right category values + a conversation_distillation_state
row marking the conversation done.

Uses real local Supabase so the RLS-scoped INSERT contract on user_memory
and conversation_distillation_state is the production contract. Anthropic
is mocked — no token burn, deterministic JSON.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

# Scaffold: skips the whole module until app/agent/memory.py lands.

from app.agent import memory as memory_module  # noqa: E402
from app.db import supabase_for_user  # noqa: E402




pytestmark = pytest.mark.usefixtures("clean_memory")


@dataclass
class _Usage:
    """Minimal usage stand-in for ai_call_log writes."""
    input_tokens: int = 400
    output_tokens: int = 80


@dataclass
class _MockMessage:
    """Minimal anthropic.types.Message stand-in. Content is a single text block."""
    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default the usage if the caller omitted it."""
        if self.usage is None:
            self.usage = _Usage()


class _ScriptedClient:
    """messages.create() returns one scripted JSON-bearing _MockMessage."""

    def __init__(self, json_payload: list[dict[str, Any]]):
        """Initialize with the JSON list Haiku is expected to return."""
        self.calls: list[dict[str, Any]] = []
        payload_text = json.dumps(json_payload)
        outer = self

        class _Messages:
            """Inner namespace mirroring the SDK's `.messages.create` surface."""
            def create(self, **kwargs: Any) -> _MockMessage:
                """Record kwargs and return the scripted payload as a text block."""
                outer.calls.append(kwargs)
                return _MockMessage(
                    content=[{"type": "text", "text": payload_text}],
                )

        self.messages = _Messages()


def test_distill_session_writes_facts_and_marks_conversation_done(
    user_a, monkeypatch,
):
    """A 6-message conversation about a CSR Q2 SUB and Costco-on-CSR
    produces two user_memory rows (one `goal`, one `card_preference`)
    and one conversation_distillation_state row keyed on the
    conversation_id."""
    conversation_id = uuid.uuid4()
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user",      "I'm trying to hit my CSR $4K spend bonus by Q2"),
            ("assistant", "Got it. How much have you spent so far this quarter?"),
            ("user",      "About $2.1K. I put my Costco runs on CSR for the points."),
            ("assistant", "Noted. Costco accepts Visa only — CSR works there."),
            ("user",      "Yeah that's why. Anyway what's my dining total this month?"),
            ("assistant", "Your dining total this month is $312."),
        ],
    )

    scripted = _ScriptedClient(
        json_payload=[
            {
                "fact": "User is working toward CSR $4K SUB by Q2 2026",
                "category": "goal",
                "relevance_score": 0.9,
            },
            {
                "fact": "User puts Costco purchases on CSR",
                "category": "card_preference",
                "relevance_score": 0.6,
            },
        ],
    )
    monkeypatch.setattr(memory_module, "_anthropic_client", lambda: scripted)

    memory_module.distill_session(user_a.jwt, conversation_id)

    client = supabase_for_user(user_a.jwt)
    rows = (
        client.table("user_memory")
        .select("fact, category, relevance_score")
        .order("category")
        .execute()
        .data
    )
    assert len(rows) == 2
    by_cat = {r["category"]: r for r in rows}
    assert "goal" in by_cat
    assert "card_preference" in by_cat
    assert "CSR" in by_cat["goal"]["fact"]
    assert "Costco" in by_cat["card_preference"]["fact"]

    state = (
        client.table("conversation_distillation_state")
        .select("conversation_id")
        .eq("conversation_id", str(conversation_id))
        .execute()
        .data
    )
    assert len(state) == 1

    # One Haiku call for the whole conversation — no per-turn distillation.
    assert len(scripted.calls) == 1


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
