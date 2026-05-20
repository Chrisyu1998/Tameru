"""Day 8 — agent loop unit tests with mocked Anthropic responses.

Mocks the Anthropic client; uses the real local Supabase stack so tool
execution exercises real RLS + the narrow ai_call_log INSERT policy
(CLAUDE.md invariant 14). Catches drift in the user-JWT path that pure
mocks would mask.

Scope is the loop itself — `run_turn()` semantics, tool dispatch, the
8-iteration cap, audit log writes, error recovery. Route-level wiring
(POST /chat/turn body validation, history loading, persistence,
LOOP_LIMIT response shape, auth-gate behavior) lives in
tests/routes/test_chat.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.agent import loop as loop_module
from app.agent.loop import (
    AgentLoopLimitExceeded,
    MAX_LOOP_ITERATIONS,
    run_turn,
)
from app.auth import AuthedUser
from app.db import supabase_for_user
from app.prompts.chat import PROMPT_VERSION as CHAT_PROMPT_VERSION


# ---------------------------------------------------------------------------
# Test scaffolding — a tiny stand-in for anthropic.types.Message + Block.
# ---------------------------------------------------------------------------


class _Block(dict):
    """A dict that also satisfies _block_to_dict()'s `.model_dump()` call.

    The real SDK returns pydantic blocks whose model_dump() yields a dict;
    inheriting from dict and adding model_dump means the same instance
    works for both the dispatch path (which iterates content) and the
    serialize path (which calls model_dump)."""

    def model_dump(self) -> dict[str, Any]:
        """Provide model dump."""
        return dict(self)


@dataclass
class _Usage:
    """Represent Usage."""
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class _MockMessage:
    """Represent MockMessage."""
    content: list[_Block]
    stop_reason: str
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Support post init."""
        if self.usage is None:
            self.usage = _Usage()


class _ScriptedClient:
    """Returns the next scripted response on each .messages.create() call.

    Stops scripted-response replay after the script runs out — additional
    calls raise so a runaway loop fails the test instead of hanging."""

    def __init__(self, responses: list[_MockMessage]):
        """Support the instance."""
        self._responses = list(responses)
        self.call_count = 0

        outer = self

        class _Messages:
            """Represent Messages."""
            def create(self, **_: Any) -> _MockMessage:
                """Provide create."""
                outer.call_count += 1
                if not outer._responses:
                    raise AssertionError(
                        "agent loop made more model calls than the script provided"
                    )
                return outer._responses.pop(0)

        self.messages = _Messages()


@pytest.fixture
def authed_user(user_a) -> AuthedUser:
    """Provide authed user."""
    return AuthedUser(
        jwt=user_a.jwt,
        user_id=UUID(user_a.id),
        email=user_a.email,
    )


# ---------------------------------------------------------------------------
# One-hop: model returns text directly, no tool_use.
# ---------------------------------------------------------------------------


def test_one_hop_turn_returns_text(authed_user, monkeypatch):
    """Verify that one hop turn returns text."""
    scripted = _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_text("You haven't spent anything yet.")],
                stop_reason="end_turn",
            )
        ],
    )

    turn = run_turn(authed_user, [], "How much have I spent?")

    assert turn.assistant_text == "You haven't spent anything yet."
    assert turn.tool_calls == []
    assert scripted.call_count == 1

    rows = _ai_call_log_chat_rows(authed_user)
    assert rows, "expected at least one ai_call_log row for chat_turn"
    assert rows[0]["task_type"] == "chat_turn"
    # Assert against the live constant, not a pinned literal — the loop
    # logs whatever PROMPT_VERSION chat.py currently exports, and pinning
    # a string here just means every prompt bump breaks an unrelated test.
    assert rows[0]["prompt_version"] == CHAT_PROMPT_VERSION
    assert rows[0]["success"] is True


# ---------------------------------------------------------------------------
# Two-hop: tool_use → tool_result → final text. Tool executes against real
# transactions (via RLS). Verifies the right number reaches the response.
# ---------------------------------------------------------------------------


def test_two_hop_turn_executes_tool_and_synthesizes(
    authed_user, user_a, card_a, monkeypatch
):
    """Verify that two hop turn executes tool and synthesizes."""
    merchant = f"Nobu-{uuid.uuid4().hex[:6]}"
    _seed_transaction(user_a, card_id=card_a, merchant=merchant, amount="42.50")
    _seed_transaction(user_a, card_id=card_a, merchant=merchant, amount="17.50")
    # Expected total for category=Dining: 60.00 (plus any prior Dining
    # rows in the session-scoped fixture; assert >= 60 to be robust).

    tool_use_id = "toolu_test_1"
    scripted = _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_tool_use("calculate_total", {"category": "Dining"}, tool_use_id)],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("You spent $60.00 on Dining.")],
                stop_reason="end_turn",
            ),
        ],
    )

    turn = run_turn(authed_user, [], "How much on dining?")

    assert turn.assistant_text == "You spent $60.00 on Dining."
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.name == "calculate_total"
    assert call.input == {"category": "Dining"}
    # Real tool execution against the real DB: total should be at least
    # the 60.00 we just seeded (other tests in the session may have
    # added more Dining rows).
    assert Decimal(call.result["total"]) >= Decimal("60.00")
    assert call.result["count"] >= 2
    assert call.result["truncated"] is False
    assert scripted.call_count == 2

    # Two ai_call_log rows for this turn (one per model call).
    rows = _ai_call_log_chat_rows(authed_user)
    assert len(rows) >= 2
    assert all(r["task_type"] == "chat_turn" for r in rows[:2])
    assert all(r["success"] is True for r in rows[:2])


# ---------------------------------------------------------------------------
# Eight-hop cap: a model that keeps calling tools without converging hits
# the safety stop and raises.
# ---------------------------------------------------------------------------


def test_loop_limit_enforced(authed_user, user_a, card_a, monkeypatch):
    # Script MAX_LOOP_ITERATIONS + 1 tool_use responses so the loop would
    # run forever if the cap weren't enforced. The +1 guarantees the
    # ScriptedClient never runs out of responses (which would cause an
    # AssertionError that would mask the real failure).
    """Verify that loop limit enforced."""
    responses = [
        _MockMessage(
            content=[_tool_use("calculate_total", {})],
            stop_reason="tool_use",
        )
        for _ in range(MAX_LOOP_ITERATIONS + 1)
    ]
    scripted = _install_scripted_anthropic(monkeypatch, responses)

    with pytest.raises(AgentLoopLimitExceeded):
        run_turn(authed_user, [], "loop forever please")

    assert scripted.call_count == MAX_LOOP_ITERATIONS


# ---------------------------------------------------------------------------
# Unknown tool name: surfaced as an is_error tool_result, not an exception.
# ---------------------------------------------------------------------------


def test_unknown_tool_recovers_via_error_result(authed_user, monkeypatch):
    """Verify that unknown tool recovers via error result."""
    scripted = _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_tool_use("phantom_tool", {"foo": "bar"})],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("I can't do that.")],
                stop_reason="end_turn",
            ),
        ],
    )

    turn = run_turn(authed_user, [], "use a tool that doesn't exist")

    assert turn.assistant_text == "I can't do that."
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "phantom_tool"
    assert turn.tool_calls[0].result["error"] == "unknown_tool"
    assert scripted.call_count == 2


# ---------------------------------------------------------------------------
# Block sanitization — Anthropic streaming SDK leaks fields like
# `parsed_output` onto text-block model_dump() output. The Messages API
# rejects them on inbound with `Extra inputs are not permitted`, which
# 400s turn 2 when the loop replays turn 1's history. Both the serialize
# path (_block_to_dict, called every iteration) and the hydrate path
# (_clean_block_dict, called by chat.py:_load_history) must strip them.
# ---------------------------------------------------------------------------


def test_block_to_dict_strips_streaming_only_fields():
    """A streaming text block with `parsed_output` round-trips to API-clean.

    The block dict subclass mirrors how the Anthropic SDK's streaming
    helper exposes blocks: model_dump() emits all attributes including
    streaming-only ones. After serialization only the API-valid subset
    must remain, or the next turn's request body 400s the moment
    Anthropic parses it.
    """
    block = _Block(
        type="text",
        text="hello there",
        parsed_output={"any": "thing"},
        citations=None,
    )
    cleaned = loop_module._block_to_dict(block)
    assert cleaned == {"type": "text", "text": "hello there"}


def test_clean_block_dict_strips_extras_from_plain_dicts():
    """The hydrate path reads raw dicts from chat_turn_trace JSONB — no
    pydantic model in scope. _clean_block_dict must scrub them anyway so
    stale rows persisted before this fix don't keep failing turn 2."""
    stale = {
        "type": "text",
        "text": "from a stale trace row",
        "parsed_output": {"leftover": True},
    }
    cleaned = loop_module._clean_block_dict(stale)
    assert cleaned == {"type": "text", "text": "from a stale trace row"}


def test_clean_block_dict_preserves_tool_use_shape():
    """Defensive: a tool_use block's required fields survive the scrub
    even if the SDK pads it with future extras."""
    block = {
        "type": "tool_use",
        "id": "toolu_abc",
        "name": "calculate_total",
        "input": {"category": "Dining"},
        "future_field": "ignored",
    }
    cleaned = loop_module._clean_block_dict(block)
    assert cleaned == {
        "type": "tool_use",
        "id": "toolu_abc",
        "name": "calculate_total",
        "input": {"category": "Dining"},
    }


def test_clean_block_dict_passes_through_unknown_types():
    """Forward-compat: an unfamiliar block type goes through unchanged so
    a new Anthropic block kind doesn't get silently dropped to {} by an
    overly aggressive allowlist."""
    block = {"type": "future_block", "novel_field": 42}
    cleaned = loop_module._clean_block_dict(block)
    assert cleaned == block


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _text(text: str) -> _Block:
    """Support text."""
    return _Block(type="text", text=text)

def _tool_use(name: str, tool_input: dict[str, Any], use_id: str | None = None) -> _Block:
    """Support tool use."""
    return _Block(
        type="tool_use",
        id=use_id or f"toolu_{uuid.uuid4().hex[:8]}",
        name=name,
        input=tool_input,
    )

@pytest.fixture(autouse=True)
def _set_anthropic_api_key(monkeypatch):
    """The loop's lazy client init asserts ANTHROPIC_API_KEY is set even
    though we monkeypatch the client itself. Set a dummy value so the
    assertion path is exercised but we never make a real network call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-real")
    # Reset the module's cached client so a prior test's mock doesn't leak.
    monkeypatch.setattr(loop_module, "_client", None)

def _install_scripted_anthropic(monkeypatch, responses: list[_MockMessage]) -> _ScriptedClient:
    """Support install scripted anthropic."""
    scripted = _ScriptedClient(responses)
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: scripted)
    return scripted

def _seed_transaction(
    user, *, card_id: str, merchant: str, amount: str, category: str = "Dining"
) -> str:
    """Insert one transaction via the user's RLS-scoped client and return its id."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("transactions")
        .insert(
            {
                "user_id": user.id,
                "card_id": card_id,
                "merchant": merchant,
                "amount": amount,
                "date": "2026-04-01",
                "category": category,
                "source": "manual",
                "client_request_id": str(uuid.uuid4()),
            }
        )
        .execute()
    )
    return resp.data[0]["id"]

def _ai_call_log_chat_rows(user: AuthedUser) -> list[dict[str, Any]]:
    """Support ai call log chat rows."""
    client = supabase_for_user(user.jwt)
    return (
        client.table("ai_call_log")
        .select("model, task_type, prompt_version, success, error_code")
        .eq("user_id", str(user.user_id))
        .eq("task_type", "chat_turn")
        .order("timestamp", desc=True)
        .execute()
        .data
        or []
    )
