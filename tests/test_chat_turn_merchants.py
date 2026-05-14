"""Day 9c — end-to-end: chat turn carries the merchant block to Claude.

Wires the real run_turn() against a scripted Anthropic client that
captures messages.create() kwargs. Verifies the `system` field on the
captured call is the two-block content array, that block 1 contains the
seeded canonical merchant, and that block 0 stays free of per-user
content. This is the integration-level proof that Day 9a's loop +
Day 9c's prompt assembly compose correctly — unit tests on either side
would let a wiring bug slip through.

Mocks the model so no Anthropic credit burns; uses real Supabase + RLS
so the view + merchant query path matches production.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from app.agent import loop as loop_module
from app.agent.loop import run_turn
from app.auth import AuthedUser
from app.db import supabase_for_user
from app.prompts.chat import SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Scripted Anthropic client that records every messages.create() kwargs.
# ---------------------------------------------------------------------------


class _Block(dict):
    """Dict + .model_dump() to match the real SDK block shape."""

    def model_dump(self) -> dict[str, Any]:
        """Provide model dump."""
        return dict(self)


@dataclass
class _Usage:
    """Minimal usage stand-in for ai_call_log writes."""
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class _MockMessage:
    """Minimal anthropic.types.Message stand-in."""
    content: list[_Block]
    stop_reason: str
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default the usage if the caller omitted it."""
        if self.usage is None:
            self.usage = _Usage()


class _RecordingClient:
    """Captures every messages.create() kwargs into `calls`.

    The loop calls messages.create per hop; for this test one scripted
    response is enough because we synthesize an end_turn immediately.
    """

    def __init__(self, responses: list[_MockMessage]):
        """Initialize the recording client with the scripted response list."""
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

        outer = self

        class _Messages:
            """Inner namespace for the SDK's .messages.create surface."""
            def create(self, **kwargs: Any) -> _MockMessage:
                """Record kwargs and return the next scripted response."""
                outer.calls.append(kwargs)
                if not outer._responses:
                    raise AssertionError(
                        "run_turn made more model calls than the script provided"
                    )
                return outer._responses.pop(0)

        self.messages = _Messages()


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_turn_passes_two_block_system_with_merchant_canonicalization(
    user_a, card_a, monkeypatch,
):
    """End-to-end: seed a canonical merchant for user A; run a chat turn
    that mentions the variant ("KFC"); inspect the captured
    messages.create kwargs.

    Asserts:
      * `system` arrives as a list of two content blocks (not a string).
      * Block 0 equals SYSTEM_PROMPT exactly and carries cache_control.
      * Block 1 contains the seeded canonical merchant ("Kentucky Fried
        Chicken …") so Claude sees what to canonicalize against.
      * The user's typed message lives in messages[0], not in the
        cached prefix.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-real")
    monkeypatch.setattr(loop_module, "_client", None)

    tag = uuid.uuid4().hex[:8]
    canonical = f"Kentucky Fried Chicken {tag}"
    # Seed five visits so the merchant lands in the top_user_merchants view.
    for _ in range(5):
        _seed_transaction(
            user_a, card_id=card_a,
            merchant=canonical, amount="10.00",
        )

    recording = _RecordingClient(
        [
            _MockMessage(
                content=[_Block(type="text", text="ok, want me to add a $10 KFC charge?")],
                stop_reason="end_turn",
            )
        ]
    )
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: recording)

    authed = AuthedUser(jwt=user_a.jwt, user_id=UUID(user_a.id), email=user_a.email)
    run_turn(authed, [], "spent $10 at KFC")

    assert len(recording.calls) == 1
    captured = recording.calls[0]

    system = captured["system"]
    assert isinstance(system, list), "system must be a block list, not a string"
    assert len(system) == 2

    # Block 0: static preamble + cache breakpoint.
    assert system[0]["text"] == SYSTEM_PROMPT
    assert system[0].get("cache_control") == {"type": "ephemeral"}

    # Block 1: dynamic tail carries the date + the canonical merchant
    # so Claude sees what to canonicalize "KFC" against.
    assert "Today is" in system[1]["text"]
    assert canonical in system[1]["text"]
    assert "cache_control" not in system[1]

    # The user's typed message must not leak into either system block —
    # that would change the cached prefix and break the multi-user share.
    # (Substring "KFC" does appear in block 1 as part of the
    # canonicalization framing — "KFC ≈ Kentucky Fried Chicken" — so we
    # check the full typed message instead of the substring.)
    typed = "spent $10 at KFC"
    assert typed not in system[0]["text"]
    assert typed not in system[1]["text"]
    # But it must be in the messages list as the user turn.
    assert captured["messages"][0]["role"] == "user"
    assert captured["messages"][0]["content"] == typed


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _seed_transaction(
    user,
    *,
    card_id: str | None,
    merchant: str,
    amount: str,
    category: str = "Dining",
) -> str:
    """Insert one transaction via the user's RLS-scoped client; return id."""
    client = supabase_for_user(user.jwt)
    row: dict[str, object] = {
        "user_id": user.id,
        "merchant": merchant,
        "amount": amount,
        "date": date.today().isoformat(),
        "category": category,
        "source": "manual",
        "client_request_id": str(uuid.uuid4()),
    }
    if card_id is not None:
        row["card_id"] = card_id
    return client.table("transactions").insert(row).execute().data[0]["id"]
