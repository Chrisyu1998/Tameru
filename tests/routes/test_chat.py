"""Day 8 — `app/routes/chat.py` contract.

Covers POST /chat/turn at the HTTP layer:

- Body validation (empty / missing message → 422).
- Auth-gate behavior (no JWT, missing X-Device-Id → 401 with structured
  payloads). The gate itself (`get_current_user_with_device`) has its
  own dedicated tests in tests/test_auth.py exercised via the
  transactions routes; this file's coverage is one assertion per branch
  to confirm the chat route is actually wired through it.
- Response shape: `{conversation_id, assistant_text, tool_calls: [...]}`
  with `tool_calls` carrying the per-iteration trace Day 10's UI
  consumes.
- Conversation lifecycle: a turn without `conversation_id` mints one;
  a follow-up turn with that id reuses it AND loads prior history.
- Persistence: both user and assistant rows land under one
  conversation_id with role-correct content_blocks.
- Loop-cap failure mode: `LOOP_LIMIT` 500 + zero rows persisted for
  that turn.

Anthropic is mocked end-to-end. Tool execution + persistence + the
ai_call_log writer all hit the real local Supabase stack so RLS
behavior under the route is exercised for real (CLAUDE.md invariants
1, 14).
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import loop as loop_module
from app.agent.loop import MAX_LOOP_ITERATIONS
from app.db import supabase_for_user
from app.main import app


# ---------------------------------------------------------------------------
# Tiny stand-ins for anthropic.types.Message + Block (mirrors the helpers in
# tests/test_agent_loop.py — kept local so this file is self-contained).
# ---------------------------------------------------------------------------


class _Block(dict):
    def model_dump(self) -> dict[str, Any]:
        return dict(self)


def _text(text: str) -> _Block:
    return _Block(type="text", text=text)


def _tool_use(name: str, tool_input: dict[str, Any], use_id: str | None = None) -> _Block:
    return _Block(
        type="tool_use",
        id=use_id or f"toolu_{uuid.uuid4().hex[:8]}",
        name=name,
        input=tool_input,
    )


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class _MockMessage:
    content: list[_Block]
    stop_reason: str
    usage: _Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage is None:
            self.usage = _Usage()


class _ScriptedClient:
    def __init__(self, responses: list[_MockMessage]):
        self._responses = list(responses)
        self.call_count = 0
        # Capture the messages payload of every call so tests can assert
        # that prior history was actually replayed to the model.
        self.recorded_calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            def create(self, **kwargs: Any) -> _MockMessage:
                outer.call_count += 1
                # Deep-copy: the loop mutates the messages list it passed
                # (appending the assistant response and tool_results) AFTER
                # the call returns. Without snapshotting, recorded_calls
                # would always reflect the post-mutation state, hiding
                # what was actually sent on this iteration.
                outer.recorded_calls.append(copy.deepcopy(kwargs))
                if not outer._responses:
                    raise AssertionError(
                        "agent loop made more model calls than the script provided"
                    )
                return outer._responses.pop(0)

        self.messages = _Messages()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _set_anthropic_api_key(monkeypatch):
    """Loop's lazy client init checks ANTHROPIC_API_KEY even though we
    monkeypatch the client. Set a dummy and reset the cached client so
    a prior test's mock doesn't leak."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-real")
    monkeypatch.setattr(loop_module, "_client", None)


def _install_scripted_anthropic(monkeypatch, responses: list[_MockMessage]) -> _ScriptedClient:
    scripted = _ScriptedClient(responses)
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: scripted)
    return scripted


def _auth(user) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _seed_transaction(
    user, *, card_id: str, merchant: str, amount: str, category: str = "Dining"
) -> str:
    sb = supabase_for_user(user.jwt)
    resp = (
        sb.table("transactions")
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


def _chat_rows(user, conversation_id: str) -> list[dict[str, Any]]:
    sb = supabase_for_user(user.jwt)
    return (
        sb.table("chat_messages")
        .select("role, content_blocks, seq")
        .eq("conversation_id", conversation_id)
        # Order by seq, not created_at — see app/routes/chat.py for why.
        .order("seq")
        .execute()
        .data
        or []
    )


def _trace_rows(user, conversation_id: str) -> list[dict[str, Any]]:
    sb = supabase_for_user(user.jwt)
    return (
        sb.table("chat_turn_trace")
        .select("messages, seq")
        .eq("conversation_id", conversation_id)
        .order("seq")
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


def test_empty_message_is_rejected(client, user_a, monkeypatch):
    # No mock installed: an accepted request would explode with a missing-
    # client error, which would still fail the test but for the wrong
    # reason. Pre-installing an empty script is harmless because we
    # expect 422 before the loop runs.
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post("/chat/turn", headers=_auth(user_a), json={"message": ""})
    assert resp.status_code == 422


def test_missing_message_is_rejected(client, user_a, monkeypatch):
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post("/chat/turn", headers=_auth(user_a), json={})
    assert resp.status_code == 422


def test_extra_fields_are_rejected(client, user_a, monkeypatch):
    """`extra='forbid'` on ChatTurnRequest. Catches client typos like
    `messages` (plural) instead of returning a confusing empty turn."""
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "hi", "messagez": "typo"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auth gate — chat route is wired through get_current_user_with_device.
# Keep these to one assertion per branch; the full auth matrix lives in
# tests/test_auth.py and tests/routes/test_auth.py.
# ---------------------------------------------------------------------------


def test_missing_jwt_returns_401(client, user_a, monkeypatch):
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post(
        "/chat/turn",
        headers={"X-Device-Id": user_a.device_id or ""},
        json={"message": "hi"},
    )
    assert resp.status_code == 401


def test_missing_device_id_returns_structured_401(client, user_a, monkeypatch):
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post(
        "/chat/turn",
        headers={"Authorization": f"Bearer {user_a.jwt}"},
        json={"message": "hi"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MISSING_DEVICE_ID"


# ---------------------------------------------------------------------------
# Response shape + persistence — happy path, no tools.
# ---------------------------------------------------------------------------


def test_turn_mints_conversation_id_and_persists_both_tables(client, user_a, monkeypatch):
    _install_scripted_anthropic(
        monkeypatch,
        [_MockMessage(content=[_text("Sure thing.")], stop_reason="end_turn")],
    )

    resp = client.post("/chat/turn", headers=_auth(user_a), json={"message": "hi"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["assistant_text"] == "Sure thing."
    assert body["tool_calls"] == []
    conversation_id = body["conversation_id"]
    # Validates as a UUID — the route mints one when the body omits it.
    uuid.UUID(conversation_id)

    # chat_messages: human-visible log, alternating user/assistant.
    rows = _chat_rows(user_a, conversation_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content_blocks"] == [{"type": "text", "text": "hi"}]
    assert any(b.get("type") == "text" for b in rows[1]["content_blocks"])

    # chat_turn_trace: one row per turn, full message-list slice.
    traces = _trace_rows(user_a, conversation_id)
    assert len(traces) == 1
    msgs = traces[0]["messages"]
    assert msgs[0] == {"role": "user", "content": "hi"}
    assert msgs[-1]["role"] == "assistant"
    assert any(b.get("type") == "text" for b in msgs[-1]["content"])


# ---------------------------------------------------------------------------
# Two-hop turn surfaces tool_calls in the response (Day 10 contract).
# ---------------------------------------------------------------------------


def test_two_hop_turn_returns_tool_calls(client, user_a, card_a, monkeypatch):
    merchant = f"Nobu-{uuid.uuid4().hex[:6]}"
    _seed_transaction(user_a, card_id=card_a, merchant=merchant, amount="42.50")

    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_tool_use("calculate_total", {"category": "Dining"})],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("You spent $42.50 on Dining.")],
                stop_reason="end_turn",
            ),
        ],
    )

    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "how much on dining?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["assistant_text"] == "You spent $42.50 on Dining."
    assert len(body["tool_calls"]) == 1
    call = body["tool_calls"][0]
    assert call["name"] == "calculate_total"
    assert call["input"] == {"category": "Dining"}
    assert "total" in call["result"] and "count" in call["result"]


# ---------------------------------------------------------------------------
# Conversation continuity — providing conversation_id reuses it AND replays
# history to the model.
# ---------------------------------------------------------------------------


def test_conversation_id_reuse_loads_prior_history(client, user_a, monkeypatch):
    # Turn 1 — mint a conversation_id.
    _install_scripted_anthropic(
        monkeypatch,
        [_MockMessage(content=[_text("Got it.")], stop_reason="end_turn")],
    )
    first = client.post("/chat/turn", headers=_auth(user_a), json={"message": "remember X"})
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    # Turn 2 — reuse conversation_id; assert the model sees prior turns.
    scripted = _install_scripted_anthropic(
        monkeypatch,
        [_MockMessage(content=[_text("Yes, X.")], stop_reason="end_turn")],
    )
    second = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"conversation_id": conversation_id, "message": "what was X?"},
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    # Single recorded call: turn-1 user + turn-1 assistant final + turn-2 user.
    sent_messages = scripted.recorded_calls[0]["messages"]
    roles = [m["role"] for m in sent_messages]
    assert roles == ["user", "assistant", "user"], (
        f"history wasn't replayed; sent roles={roles}"
    )

    # chat_messages: 4 rows (2 per turn), still clean alternation.
    rows = _chat_rows(user_a, conversation_id)
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]

    # chat_turn_trace: 2 rows (1 per turn).
    traces = _trace_rows(user_a, conversation_id)
    assert len(traces) == 2


# ---------------------------------------------------------------------------
# Two-table semantic — multi-hop turn replays tool_use / tool_result on the
# next turn (the bug Codex flagged). chat_messages stays clean across the
# multi-hop turn; chat_turn_trace carries the full block sequence forward.
# ---------------------------------------------------------------------------


def test_multi_hop_turn_replays_tool_context_on_followup(
    client, user_a, card_a, monkeypatch
):
    # Seed enough Dining for the first tool call to return a real number.
    _seed_transaction(
        user_a, card_id=card_a, merchant=f"Nobu-{uuid.uuid4().hex[:6]}", amount="42.50"
    )

    # Turn 1: two-hop — calculate_total then prose.
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_tool_use("calculate_total", {"category": "Dining"})],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("You spent $42.50 on Dining.")],
                stop_reason="end_turn",
            ),
        ],
    )
    first = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "how much on dining?"},
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    # chat_messages stays clean: still exactly 2 rows for this turn (user
    # + assistant-final), no synthetic tool_result rows polluting the
    # human-visible thread. This is the load-bearing UI property.
    rows = _chat_rows(user_a, conversation_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    for r in rows:
        for block in r["content_blocks"]:
            assert block.get("type") == "text", (
                f"chat_messages contains a non-text block ({block.get('type')!r}); "
                "synthetic tool_use / tool_result blocks belong in chat_turn_trace only"
            )

    # chat_turn_trace, by contrast, carries the FULL block sequence.
    traces = _trace_rows(user_a, conversation_id)
    assert len(traces) == 1
    msgs = traces[0]["messages"]
    # Expect: [user-text, assistant-with-tool_use, user-with-tool_result, assistant-final].
    types_per_message = [
        (m["role"], [b.get("type") for b in m["content"]] if isinstance(m["content"], list) else "text")
        for m in msgs
    ]
    assert types_per_message[0][0] == "user"
    assert "tool_use" in (types_per_message[1][1] or []), (
        f"trace missing tool_use on iter-1 assistant block: {types_per_message}"
    )
    assert "tool_result" in (types_per_message[2][1] or []), (
        f"trace missing tool_result on synthetic user block: {types_per_message}"
    )
    assert types_per_message[3][0] == "assistant"

    # Turn 2: a follow-up that depends on prior tool context. The
    # critical assertion is what reaches Claude on the second call —
    # the prior tool_use + tool_result blocks must be present, not just
    # the prose.
    scripted = _install_scripted_anthropic(
        monkeypatch,
        [_MockMessage(content=[_text("$0 on coffee.")], stop_reason="end_turn")],
    )
    second = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"conversation_id": conversation_id, "message": "what about coffee?"},
    )
    assert second.status_code == 200

    sent = scripted.recorded_calls[0]["messages"]
    # Turn-1 contributed 4 messages (user, assistant-tool_use,
    # user-tool_result, assistant-final); turn-2 adds the new user
    # message. Five total.
    assert len(sent) == 5, (
        f"expected 5 messages (4 from turn-1 trace + 1 new user); got "
        f"{len(sent)}: {[m['role'] for m in sent]}"
    )
    # Verify the tool_use / tool_result blocks specifically made it
    # through replay — not just the count.
    second_msg_blocks = sent[1]["content"]
    third_msg_blocks = sent[2]["content"]
    assert any(b.get("type") == "tool_use" for b in second_msg_blocks), (
        "tool_use from turn 1 was lost on replay — chat_turn_trace is "
        "the source of truth for this and it should preserve the block"
    )
    assert any(b.get("type") == "tool_result" for b in third_msg_blocks), (
        "tool_result from turn 1 was lost on replay"
    )


# ---------------------------------------------------------------------------
# Loop-cap failure mode — 500 with structured code AND no rows persisted.
# ---------------------------------------------------------------------------


def test_loop_cap_returns_500_and_persists_nothing(client, user_a, monkeypatch):
    # Script MAX_LOOP_ITERATIONS + 1 so the assertion in _ScriptedClient
    # never fires — we want the loop's own cap to be the failure mode.
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_tool_use("calculate_total", {})],
                stop_reason="tool_use",
            )
            for _ in range(MAX_LOOP_ITERATIONS + 1)
        ],
    )

    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "loop forever please"},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "LOOP_LIMIT"

    # Critical: nothing persisted in either table for this attempt —
    # the prompt's "Done when" rule for the 8-hop cap.
    sb = supabase_for_user(user_a.jwt)
    matching = (
        sb.table("chat_messages")
        .select("id, content_blocks")
        .eq("role", "user")
        .execute()
        .data
        or []
    )
    user_messages = [
        row
        for row in matching
        if any(
            (b.get("text") or "") == "loop forever please"
            for b in row["content_blocks"]
        )
    ]
    assert user_messages == [], (
        "LOOP_LIMIT path persisted a chat_messages row; it should drop the turn"
    )

    # And no trace row either — the route writes trace first then
    # chat_messages, but the loop raises BEFORE either write happens.
    traces = sb.table("chat_turn_trace").select("messages").execute().data or []
    contaminated = [
        t
        for t in traces
        if any(
            (m.get("content") if isinstance(m.get("content"), str) else "")
            == "loop forever please"
            for m in t["messages"]
        )
    ]
    assert contaminated == [], (
        "LOOP_LIMIT path persisted a chat_turn_trace row; it should drop the turn"
    )
