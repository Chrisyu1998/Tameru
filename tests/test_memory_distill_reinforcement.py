"""Day 16 — re-distilling an already-seen fact reinforces, doesn't duplicate.

Pre-seed user_memory with one fact; run distill_session on a fresh
conversation that re-mentions the same fact; assert the existing row's
`reinforced_at` advances and no second row is created. The unique
index `(user_id, category, lower(fact))` is what makes the upsert do
the right thing — this test is the contract guarantee.

A second assertion: `relevance_score` only ever moves up via
`GREATEST(...)`. A low-confidence re-extraction must not downgrade a
high-confidence past assessment.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import pytest


from app.agent import memory as memory_module  # noqa: E402
from app.db import supabase_for_user  # noqa: E402




pytestmark = pytest.mark.usefixtures("clean_memory")


@dataclass
class _Usage:
    """Minimal usage stand-in for ai_call_log writes."""
    input_tokens: int = 200
    output_tokens: int = 40


@dataclass
class _MockMessage:
    """Minimal anthropic.types.Message stand-in."""
    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default usage if omitted."""
        if self.usage is None:
            self.usage = _Usage()


class _ScriptedClient:
    """Returns a single scripted response."""
    def __init__(self, json_payload):
        """Initialize with the JSON list Haiku is expected to return."""
        outer = self
        text = json.dumps(json_payload)

        class _Messages:
            """Inner messages namespace."""
            def create(self, **kwargs):
                """Return a text-block message carrying `text`."""
                outer.last_kwargs = kwargs
                return _MockMessage(content=[{"type": "text", "text": text}])

        self.messages = _Messages()
        self.last_kwargs: dict[str, Any] = {}


def test_re_distilling_existing_fact_advances_reinforced_at(user_a, monkeypatch):
    """Same fact text returned by Haiku on a new conversation updates the
    existing row instead of inserting a duplicate; reinforced_at moves
    forward; relevance_score never decreases."""
    fact_text = "User is working toward CSR $4K SUB by Q2 2026"

    client = supabase_for_user(user_a.jwt)
    # Seed: one fact, high score, two-week-old reinforcement.
    seeded = (
        client.table("user_memory")
        .insert(
            {
                "user_id": user_a.id,
                "fact": fact_text,
                "category": "goal",
                "relevance_score": 0.9,
                # The default `reinforced_at = now()` would race the
                # assertion below — bypass by writing an explicit past
                # timestamp.
                "reinforced_at": (
                    dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)
                ).isoformat(),
            }
        )
        .execute()
        .data[0]
    )
    seeded_id = seeded["id"]
    seeded_reinforced_at = seeded["reinforced_at"]

    # Conversation that re-mentions the goal.
    conversation_id = uuid.uuid4()
    _seed_conversation(
        user_a, conversation_id,
        turns=[
            ("user",      "Made another $800 of reimbursable travel on my CSR"),
            ("assistant", "Nice — you're $1.1K from the $4K Q2 SUB."),
            ("user",      "Cool, what's left for dining this month?"),
            ("assistant", "$188 under your $500 dining goal."),
        ],
    )

    # Haiku returns the SAME fact text but with a lower score — the
    # GREATEST clause must keep the old 0.9.
    scripted = _ScriptedClient(
        json_payload=[
            {
                "fact": fact_text,
                "category": "goal",
                "relevance_score": 0.4,
            }
        ],
    )
    monkeypatch.setattr(memory_module, "_anthropic_client", lambda: scripted)

    # Small sleep so reinforced_at strictly advances past the seeded value.
    time.sleep(0.01)
    memory_module.distill_session(user_a.jwt, conversation_id)

    rows = (
        client.table("user_memory")
        .select("id, fact, relevance_score, reinforced_at")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert len(rows) == 1, "duplicate fact row created — dedup index broken"
    assert rows[0]["id"] == seeded_id, "row id changed — was a delete-then-insert"
    assert float(rows[0]["relevance_score"]) == 0.9, "GREATEST clause missing"
    assert rows[0]["reinforced_at"] > seeded_reinforced_at


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
