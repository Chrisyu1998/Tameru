"""Day 12 — `app/routes/chat.py` SSE contract.

Covers POST /chat/turn at the HTTP layer after the Day 12 streaming
upgrade:

- Body validation (empty / missing message → 422 BEFORE the stream
  opens — Pydantic runs first, response stays JSON).
- Auth-gate behavior (no JWT, missing X-Device-Id → 401 with structured
  payloads, also pre-stream).
- SSE happy-path: response is `text/event-stream`, frames arrive in the
  expected order (`token`* → `tool_use`? → `token`* → `done`), and the
  `done` frame's `tool_calls` array matches Day 8's exact `{name, input,
  result}` shape (Day 10 compat contract).
- Persistence semantics: on `done`, BOTH `chat_turn_trace` and
  `chat_messages` rows land exactly once. On the loop-cap error path,
  NEITHER table sees a row — that's what makes a client retry idempotent
  (DESIGN.md §7.5).
- Loop-cap surfaces as an SSE `error` frame with `code: "LOOP_LIMIT"`,
  HTTP status 200 (not 500 — the response is already open by then).
- Conversation continuity: providing `conversation_id` reuses it AND
  replays prior history (tool_use + tool_result blocks intact) on the
  next turn.

Anthropic is mocked via `_ScriptedStreamClient` — same fixtures as the
non-streaming Day 8 mock but the inner `messages.stream(...)` returns
an iterable context manager that yields synthetic stream events
(`text`, `content_block_stop`) before `get_final_message()` returns the
scripted `_MockMessage`. Tool execution + persistence + the
ai_call_log writer all hit the real local Supabase stack so RLS
behavior under the route is exercised for real (CLAUDE.md invariants
1, 14).
"""

from __future__ import annotations

import copy
import json
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
# Tiny stand-ins for anthropic.types.Message + Block.
# Kept dict-subclass-with-model_dump so the loop's _block_to_dict helper
# round-trips them the way it round-trips real SDK pydantic blocks.
# ---------------------------------------------------------------------------


class _Block(dict):
    """Represent Block."""
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


# ---------------------------------------------------------------------------
# Scripted streaming Anthropic client.
#
# `messages.stream(**kwargs)` returns a `_ScriptedStream` — a context
# manager that:
#   * yields `_TextEvent` for each text block (one chunk per block; the
#     loop's token handler doesn't care about delta granularity).
#   * yields `_ContentBlockStopEvent` for each tool_use block, carrying
#     a `.content_block` with `.type == "tool_use"` + `.name` + `.input`.
#   * exposes `get_final_message()` returning the original `_MockMessage`,
#     which gives the loop a real `.content`, `.stop_reason`, `.usage`.
# ---------------------------------------------------------------------------


@dataclass
class _TextEvent:
    """Represent TextEvent."""
    text: str
    type: str = "text"


@dataclass
class _ContentBlockStopEvent:
    """Represent ContentBlockStopEvent."""
    content_block: Any
    type: str = "content_block_stop"


class _ToolUseBlock:
    """Represent ToolUseBlock — minimal duck-type the loop reads via getattr."""
    def __init__(self, name: str, tool_input: dict[str, Any]):
        """Support the instance."""
        self.type = "tool_use"
        self.name = name
        self.input = tool_input


class _ScriptedStream:
    """Represent ScriptedStream."""
    def __init__(self, message: _MockMessage):
        """Support the instance."""
        self._message = message

    def __enter__(self) -> "_ScriptedStream":
        """Provide enter."""
        return self

    def __exit__(self, *exc: Any) -> None:
        """Support exit."""
        return None

    def __iter__(self):
        """Yield streaming events that mirror the SDK's `messages.stream()`
        event sequence for our scripted final message."""
        for block in self._message.content:
            btype = block.get("type")
            if btype == "text":
                yield _TextEvent(text=block.get("text", ""))
            elif btype == "tool_use":
                yield _ContentBlockStopEvent(
                    content_block=_ToolUseBlock(
                        name=block.get("name", ""),
                        tool_input=block.get("input", {}) or {},
                    )
                )

    def get_final_message(self) -> _MockMessage:
        """Support get final message."""
        return self._message


class _ScriptedStreamClient:
    """Represent ScriptedStreamClient."""
    def __init__(self, responses: list[_MockMessage]):
        """Support the instance."""
        self._responses = list(responses)
        self.call_count = 0
        # Capture the messages payload of every call so tests can assert
        # that prior history was actually replayed to the model.
        self.recorded_calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            """Represent Messages."""
            def stream(self, **kwargs: Any) -> _ScriptedStream:
                """Provide stream — context manager mirroring the real SDK."""
                outer.call_count += 1
                # Deep-copy: the loop mutates the messages list it passed
                # after the call returns. Without snapshotting, recorded_calls
                # would reflect the post-mutation state.
                outer.recorded_calls.append(copy.deepcopy(kwargs))
                if not outer._responses:
                    raise AssertionError(
                        "agent loop made more model calls than the script provided"
                    )
                return _ScriptedStream(outer._responses.pop(0))

        self.messages = _Messages()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Provide client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Body validation — runs BEFORE the stream opens, so still HTTP JSON 422.
# ---------------------------------------------------------------------------


def test_empty_message_is_rejected(client, user_a, monkeypatch):
    # No mock installed: an accepted request would explode with a missing-
    # client error, which would still fail the test but for the wrong
    # reason. Pre-installing an empty script is harmless because we
    # expect 422 before the loop runs.
    """Verify that empty message is rejected."""
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post("/chat/turn", headers=_auth(user_a), json={"message": ""})
    assert resp.status_code == 422


def test_missing_message_is_rejected(client, user_a, monkeypatch):
    """Verify that missing message is rejected."""
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
# Auth gate — runs BEFORE the stream opens.
# ---------------------------------------------------------------------------


def test_missing_jwt_returns_401(client, user_a, monkeypatch):
    """Verify that missing jwt returns 401."""
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post(
        "/chat/turn",
        headers={"X-Device-Id": user_a.device_id or ""},
        json={"message": "hi"},
    )
    assert resp.status_code == 401


def test_missing_device_id_returns_structured_401(client, user_a, monkeypatch):
    """Verify that missing device id returns structured 401."""
    _install_scripted_anthropic(monkeypatch, [])
    resp = client.post(
        "/chat/turn",
        headers={"Authorization": f"Bearer {user_a.jwt}"},
        json={"message": "hi"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MISSING_DEVICE_ID"


# ---------------------------------------------------------------------------
# SSE happy path — single-iteration turn (no tools).
# ---------------------------------------------------------------------------


def test_turn_mints_conversation_id_and_persists_both_tables(client, user_a, monkeypatch):
    """Verify that turn mints conversation id and persists both tables."""
    _install_scripted_anthropic(
        monkeypatch,
        [_MockMessage(content=[_text("Sure thing.")], stop_reason="end_turn")],
    )

    resp = client.post("/chat/turn", headers=_auth(user_a), json={"message": "hi"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    # Day 12 buffering headers.
    assert "no-cache" in resp.headers["cache-control"]
    assert resp.headers["x-accel-buffering"] == "no"

    frames = _parse_sse(resp.content)
    events = [f[0] for f in frames]
    assert events[0] == "token", f"expected first frame to be a token, got {events}"
    assert events[-1] == "done", f"expected last frame to be `done`, got {events}"
    assert "error" not in events

    # Token frames concatenate to the assistant text.
    tokens = "".join(f[1] for f in frames if f[0] == "token")
    assert tokens == "Sure thing."

    # done frame carries the Day 8 shape.
    done_payload = json.loads(frames[-1][1])
    conversation_id = done_payload["conversation_id"]
    uuid.UUID(conversation_id)  # validates
    assert done_payload["tool_calls"] == []

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
# Two-hop turn surfaces tool_calls in the done frame AND a tool_use frame
# fires mid-stream (Day 12 + Day 10 contract).
# ---------------------------------------------------------------------------


def test_two_hop_turn_returns_tool_calls(client, user_a, card_a, monkeypatch):
    """Verify that two hop turn returns tool calls."""
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

    frames = _parse_sse(resp.content)
    # One tool_use frame fires when the model assembles the call.
    tool_use_frames = [f for f in frames if f[0] == "tool_use"]
    assert len(tool_use_frames) == 1
    tool_use_payload = json.loads(tool_use_frames[0][1])
    assert tool_use_payload["name"] == "calculate_total"
    assert tool_use_payload["input"] == {"category": "Dining"}

    # Final iteration's text streams as tokens.
    tokens = "".join(f[1] for f in frames if f[0] == "token")
    assert tokens == "You spent $42.50 on Dining."

    # done.tool_calls is the Day 8 shape — name/input/result.
    done_payload = json.loads(frames[-1][1])
    assert frames[-1][0] == "done"
    assert len(done_payload["tool_calls"]) == 1
    call = done_payload["tool_calls"][0]
    assert call["name"] == "calculate_total"
    assert call["input"] == {"category": "Dining"}
    assert "total" in call["result"] and "count" in call["result"]


# ---------------------------------------------------------------------------
# Propose-* tool calls embed a `tameru_proposal` block on the assistant's
# chat_messages row so GET /chat/messages can rehydrate parse cards after a
# page refresh. Pre-Day-14b, the assistant row carried only the prose, which
# left "here's the parse — tap looks right" orphaned without a card to tap.
# ---------------------------------------------------------------------------


def test_propose_transaction_persists_tameru_proposal_block(
    client, user_a, card_a, monkeypatch
):
    """Verify that a propose_transaction turn writes a tameru_proposal block.

    The agent calls `propose_transaction`; we then inspect chat_messages
    and assert the assistant row carries:
      * the prose text block (existing behavior, untouched), AND
      * a `tameru_proposal` block with the full proposal payload so the
        client can reconstruct the parse card on rehydrate.

    Unrelated tools (calculate_total etc.) MUST NOT add a proposal block —
    only `propose_transaction` and `propose_card` do.
    """
    _seed_transaction(
        user_a, card_id=card_a, merchant=f"Lupa-{uuid.uuid4().hex[:6]}", amount="42.50"
    )
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[
                    _tool_use(
                        "propose_transaction",
                        {
                            "merchant": "Blue Bottle",
                            "amount": 5.50,
                            "date": "2026-05-13",
                            "category": "Coffee Shops",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("Here's the parse — tap looks right to add it.")],
                stop_reason="end_turn",
            ),
        ],
    )

    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "5.50 on blue bottle"},
    )
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    rows = _chat_rows(user_a, conversation_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]

    assistant_blocks = rows[1]["content_blocks"]
    text_blocks = [b for b in assistant_blocks if b.get("type") == "text"]
    proposal_blocks = [
        b for b in assistant_blocks if b.get("type") == "tameru_proposal"
    ]

    assert len(text_blocks) == 1
    assert "parse" in text_blocks[0]["text"].lower()
    assert len(proposal_blocks) == 1, (
        f"expected exactly one tameru_proposal block; got {assistant_blocks!r}"
    )
    proposal = proposal_blocks[0]
    assert proposal["tool_name"] == "propose_transaction"
    assert proposal["input"]["merchant"] == "Blue Bottle"
    # The result is the TransactionProposal.model_dump(mode="json") payload
    # — merchant, amount, date, category, and a fresh client_request_id.
    assert proposal["result"]["merchant"] == "Blue Bottle"
    assert proposal["result"]["category"] == "Coffee Shops"
    uuid.UUID(proposal["result"]["client_request_id"])


def test_calculate_total_does_not_persist_tameru_proposal_block(
    client, user_a, card_a, monkeypatch
):
    """Verify that non-propose tools never produce a tameru_proposal block.

    Guards against a regression where the persistence helper widens its
    filter and accidentally surfaces e.g. calculate_total results on the
    rehydrate path. Only propose_transaction / propose_card belong there.
    """
    _seed_transaction(
        user_a, card_id=card_a, merchant=f"Lupa-{uuid.uuid4().hex[:6]}", amount="42.50"
    )
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
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    rows = _chat_rows(user_a, conversation_id)
    for r in rows:
        for block in r["content_blocks"]:
            assert block.get("type") != "tameru_proposal", (
                f"calculate_total turn leaked a tameru_proposal block: {block!r}"
            )


# ---------------------------------------------------------------------------
# GET /chat/messages reconstructs parse cards + committed state for rehydrate.
# ---------------------------------------------------------------------------


def test_get_messages_returns_tameru_proposal_blocks_for_rehydrate(
    client, user_a, card_a, monkeypatch
):
    """Verify /chat/messages exposes tameru_proposal blocks for rehydrate.

    After a propose_transaction turn, /chat/messages should return the
    assistant row with a `tameru_proposal` block on `content_blocks` so
    the client can reconstruct an interactive parse card on page refresh
    instead of orphaning "here's the parse" prose without a card.
    """
    _seed_transaction(
        user_a, card_id=card_a, merchant=f"Lupa-{uuid.uuid4().hex[:6]}", amount="42.50"
    )
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[
                    _tool_use(
                        "propose_transaction",
                        {
                            "merchant": "Blue Bottle",
                            "amount": 5.50,
                            "date": "2026-05-13",
                            "category": "Coffee Shops",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("Here's the parse — tap looks right.")],
                stop_reason="end_turn",
            ),
        ],
    )
    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "5.50 on blue bottle"},
    )
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    history = client.get(
        f"/chat/messages?conversation_id={conversation_id}",
        headers=_auth(user_a),
    )
    assert history.status_code == 200
    body = history.json()
    assert len(body["messages"]) == 2
    assistant_blocks = body["messages"][1]["content_blocks"]
    proposal_blocks = [
        b for b in assistant_blocks if b.get("type") == "tameru_proposal"
    ]
    assert len(proposal_blocks) == 1
    assert proposal_blocks[0]["tool_name"] == "propose_transaction"
    # Not yet confirmed → no committed_id / committed_state on the block.
    assert "committed_id" not in proposal_blocks[0]
    assert "committed_state" not in proposal_blocks[0]


def test_get_messages_annotates_committed_id_for_confirmed_transaction(
    client, user_a, card_a, monkeypatch
):
    """Verify the rehydrate endpoint marks already-logged proposals.

    Drive a propose_transaction turn, simulate the user tapping "looks
    right" by inserting a `transactions` row with the same
    `client_request_id`, then assert /chat/messages decorates the
    matching tameru_proposal block with `committed_id`. The UI uses this
    to render ParseCard in its locked "logged." state instead of inviting
    a duplicate confirm.
    """
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[
                    _tool_use(
                        "propose_transaction",
                        {
                            "merchant": "Sunny Coffee",
                            "amount": 4.25,
                            "date": "2026-05-13",
                            "category": "Coffee Shops",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("Here's the parse.")],
                stop_reason="end_turn",
            ),
        ],
    )
    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "4.25 coffee"},
    )
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    # Pull the proposal's client_request_id from the persisted tameru_proposal
    # block — that's the idempotency key the rehydrate lookup joins on.
    rows = _chat_rows(user_a, conversation_id)
    proposal = next(
        b for b in rows[1]["content_blocks"] if b.get("type") == "tameru_proposal"
    )
    crid = proposal["result"]["client_request_id"]

    # Simulate "looks right" → /transactions/confirm by inserting a row
    # with the same client_request_id. RLS scopes the write to user_a.
    sb = supabase_for_user(user_a.jwt)
    tx_resp = (
        sb.table("transactions")
        .insert(
            {
                "user_id": user_a.id,
                "card_id": card_a,
                "merchant": "Sunny Coffee",
                "amount": "4.25",
                "date": "2026-05-13",
                "category": "Coffee Shops",
                "source": "nlp",
                "client_request_id": crid,
            }
        )
        .execute()
    )
    tx_id = tx_resp.data[0]["id"]

    history = client.get(
        f"/chat/messages?conversation_id={conversation_id}",
        headers=_auth(user_a),
    )
    assert history.status_code == 200
    body = history.json()
    proposal_blocks = [
        b
        for b in body["messages"][1]["content_blocks"]
        if b.get("type") == "tameru_proposal"
    ]
    assert len(proposal_blocks) == 1
    assert proposal_blocks[0].get("committed_id") == tx_id
    assert proposal_blocks[0].get("committed_state") == "active"


def test_get_messages_emits_committed_payload_reflecting_edited_row(
    client, user_a, card_a, monkeypatch
):
    """Day 15: `committed_payload` carries the row's *actual* values.

    The agent proposes $4.25; the user edits to $4.99 before tapping
    "looks right"; the row lands at $4.99. The rehydrate annotation must
    surface the row's $4.99 as `committed_payload.amount` so the
    frontend's `_proposalToDraft` (which prefers `committed_payload` over
    `result`) renders the committed value, not the agent's suggestion.

    This is the load-bearing post-Day-15 contract: without
    `committed_payload`, a rehydrated `logged.` parse card would display
    the agent's original number forever even though the ledger has the
    edit.
    """
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[
                    _tool_use(
                        "propose_transaction",
                        {
                            "merchant": "Sunny Coffee",
                            "amount": 4.25,
                            "date": "2026-05-13",
                            "category": "Coffee Shops",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("Here's the parse.")],
                stop_reason="end_turn",
            ),
        ],
    )
    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "4.25 coffee"},
    )
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    rows = _chat_rows(user_a, conversation_id)
    proposal = next(
        b for b in rows[1]["content_blocks"] if b.get("type") == "tameru_proposal"
    )
    crid = proposal["result"]["client_request_id"]

    # Insert the row at $4.99 (not $4.25) — simulating the user editing
    # the amount on the parse card before tapping "looks right." Also
    # change the merchant casing to match a server-side normalization
    # the row might apply; this exercises the symmetric merchant-from-
    # committed_payload precedence.
    sb = supabase_for_user(user_a.jwt)
    tx_resp = (
        sb.table("transactions")
        .insert(
            {
                "user_id": user_a.id,
                "card_id": card_a,
                "merchant": "Sunny Coffee Roasters",
                "amount": "4.99",
                "date": "2026-05-13",
                "category": "Coffee Shops",
                "source": "nlp",
                "client_request_id": crid,
                "notes": "after edit",
            }
        )
        .execute()
    )
    tx_id = tx_resp.data[0]["id"]

    history = client.get(
        f"/chat/messages?conversation_id={conversation_id}",
        headers=_auth(user_a),
    )
    assert history.status_code == 200
    body = history.json()
    proposal_blocks = [
        b
        for b in body["messages"][1]["content_blocks"]
        if b.get("type") == "tameru_proposal"
    ]
    assert len(proposal_blocks) == 1
    block = proposal_blocks[0]
    assert block.get("committed_id") == tx_id
    assert block.get("committed_state") == "active"

    # Load-bearing assertion: committed_payload reflects the row, not
    # the proposal. `result` is untouched (audit trail) — the agent's
    # original $4.25 stays there for the rehydrate fallback.
    cp = block.get("committed_payload")
    assert cp is not None, "Day 15: committed_payload missing"
    assert cp["merchant"] == "Sunny Coffee Roasters"
    assert str(cp["amount"]) in ("4.99", "4.9900")  # numeric serialization
    assert cp["category"] == "Coffee Shops"
    assert cp["client_request_id"] == crid
    assert cp["notes"] == "after edit"
    # `result` (the proposal) stays untouched — that's the audit-trail
    # property.
    assert block["result"]["amount"] == "4.25"
    assert block["result"]["merchant"] == "Sunny Coffee"


def test_get_messages_emits_committed_payload_for_committed_card(
    client, user_a, monkeypatch
):
    """Day 15: cards also get `committed_payload` carrying row truth.

    Cards are matched on `name` (no client_request_id), so this test
    drives a propose_card turn, inserts a row whose `last_four` and
    `multipliers` differ from the proposal (mimicking the user filling
    in last-4 + adjusting multipliers post-confirm), and asserts the
    rehydrate annotation surfaces those committed values — not the
    proposal's nulls/originals.
    """
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[
                    _tool_use(
                        "propose_card",
                        {"program": "Amex Gold"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("here's the card.")],
                stop_reason="end_turn",
            ),
        ],
    )
    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "amex gold"},
    )
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    # Pull the proposal's `client_request_id` from the persisted
    # tameru_proposal block — Day 15 made it the load-bearing join key
    # for `_annotate_committed_proposals`. The test inserts the row
    # under that same crid so we exercise the new path (not the legacy
    # name fallback).
    rows = _chat_rows(user_a, conversation_id)
    proposal = next(
        b for b in rows[1]["content_blocks"] if b.get("type") == "tameru_proposal"
    )
    proposal_crid = proposal["result"]["client_request_id"]

    sb = supabase_for_user(user_a.jwt)
    card_resp = (
        sb.table("cards")
        .insert(
            {
                "user_id": user_a.id,
                "name": "Amex Gold",
                "issuer": "amex",
                "network": "amex",
                "program": "MR",
                "multipliers": {"Dining": 4, "Groceries": 4},
                "annual_fee": "325",
                "last_four": "1234",
                "source_urls": [],
                "color": None,
                "status": "active",
                "client_request_id": proposal_crid,
            }
        )
        .execute()
    )
    card_id = card_resp.data[0]["id"]

    history = client.get(
        f"/chat/messages?conversation_id={conversation_id}",
        headers=_auth(user_a),
    )
    assert history.status_code == 200
    body = history.json()
    proposal_blocks = [
        b
        for b in body["messages"][1]["content_blocks"]
        if b.get("type") == "tameru_proposal"
    ]
    assert len(proposal_blocks) == 1
    block = proposal_blocks[0]
    assert block["tool_name"] == "propose_card"
    assert block.get("committed_id") == card_id
    assert block.get("committed_state") == "active"
    cp = block.get("committed_payload")
    assert cp is not None, "Day 15: card committed_payload missing"
    assert cp["name"] == "Amex Gold"
    assert cp["last_four"] == "1234"
    assert cp["multipliers"] == {"Dining": 4, "Groceries": 4}
    # PostgREST serializes `numeric` as a number when integral, string with
    # fractional digits otherwise. Accept either shape — the frontend
    # `_proposalToCardDraft` coerces back to a string via `String(...)`.
    assert str(cp["annual_fee"]) in ("325", "325.00")
    assert cp["issuer"] == "amex"
    assert cp["network"] == "amex"


def test_get_messages_disambiguates_two_same_name_cards_by_crid(
    client, user_a, monkeypatch
):
    """Day 15: two active cards sharing a `name` annotate cleanly.

    A user can legitimately hold "Amex Gold" 1234 and "Amex Gold" 5678
    — the partial unique index is on (issuer, last_four), not name. With
    a name-only join in `_annotate_committed_proposals` (the pre-Day-15
    behavior), both proposal blocks in chat history would get annotated
    pointing at whichever row the join returned first. After the
    `client_request_id` join key was added, each proposal block joins
    1:1 to its row.

    This test drives two separate propose_card turns (each mints a
    fresh crid), inserts two cards under those crids with different
    last_four values, then asserts each proposal block carries the
    *right* row's `committed_payload`.
    """
    # Use a fresh product name that won't collide with the session-scoped
    # `Amex Gold` rows other annotation tests in this file already created
    # (the cards table persists across the session via card_a fixture +
    # earlier inserts; same-name-same-last_four collisions on insert would
    # 23505 here even though the annotation logic itself is what we want
    # to exercise).
    PROGRAM = "Disambiguation Sapphire"
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[_tool_use("propose_card", {"program": PROGRAM})],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("first.")],
                stop_reason="end_turn",
            ),
            _MockMessage(
                content=[_tool_use("propose_card", {"program": PROGRAM})],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("second.")],
                stop_reason="end_turn",
            ),
        ],
    )

    # Turn 1: first proposal.
    resp1 = client.post(
        "/chat/turn", headers=_auth(user_a), json={"message": "first"}
    )
    assert resp1.status_code == 200
    conversation_id = json.loads(_parse_sse(resp1.content)[-1][1])["conversation_id"]

    # Turn 2: second proposal (same product name, but the agent will
    # mint a fresh crid for it).
    resp2 = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={
            "message": "second",
            "conversation_id": conversation_id,
        },
    )
    assert resp2.status_code == 200

    # Extract both proposals' crids from persisted blocks.
    rows = _chat_rows(user_a, conversation_id)
    proposal_blocks = [
        b
        for r in rows
        for b in r["content_blocks"]
        if b.get("type") == "tameru_proposal"
    ]
    assert len(proposal_blocks) == 2
    crid_a = proposal_blocks[0]["result"]["client_request_id"]
    crid_b = proposal_blocks[1]["result"]["client_request_id"]
    assert crid_a != crid_b, "propose_card must mint a fresh crid per call"

    # Insert two cards under those crids, with different last_four.
    sb = supabase_for_user(user_a.jwt)
    insert_resp = (
        sb.table("cards")
        .insert(
            [
                {
                    "user_id": user_a.id,
                    "name": PROGRAM,
                    "issuer": "chase",
                    "network": "visa",
                    "program": "UR",
                    "multipliers": {"Dining": 3},
                    "last_four": "7777",
                    "source_urls": [],
                    "status": "active",
                    "client_request_id": crid_a,
                },
                {
                    "user_id": user_a.id,
                    "name": PROGRAM,
                    "issuer": "chase",
                    "network": "visa",
                    "program": "UR",
                    "multipliers": {"Dining": 3},
                    "last_four": "8888",
                    "source_urls": [],
                    "status": "active",
                    "client_request_id": crid_b,
                },
            ]
        )
        .execute()
    )
    by_crid = {r["client_request_id"]: r["id"] for r in insert_resp.data}

    history = client.get(
        f"/chat/messages?conversation_id={conversation_id}",
        headers=_auth(user_a),
    )
    assert history.status_code == 200
    body = history.json()
    annotated = [
        b
        for m in body["messages"]
        for b in m["content_blocks"]
        if b.get("type") == "tameru_proposal"
    ]
    assert len(annotated) == 2
    # Load-bearing: each proposal block points at its OWN row, not at
    # whichever same-name row the join saw first.
    block_a = next(b for b in annotated if b["result"]["client_request_id"] == crid_a)
    block_b = next(b for b in annotated if b["result"]["client_request_id"] == crid_b)
    assert block_a["committed_id"] == by_crid[crid_a]
    assert block_b["committed_id"] == by_crid[crid_b]
    assert block_a["committed_payload"]["last_four"] == "7777"
    assert block_b["committed_payload"]["last_four"] == "8888"
    # And each block's committed_payload echoes ITS crid back, not the
    # other one's.
    assert block_a["committed_payload"]["client_request_id"] == crid_a
    assert block_b["committed_payload"]["client_request_id"] == crid_b


def test_get_messages_falls_back_to_trace_for_legacy_rows(
    client, user_a, card_a, monkeypatch
):
    """Verify trace-based fallback fills in tameru_proposal blocks.

    Rows persisted before Day 14b's `_persist_turn` augmentation carry
    only prose on the assistant `content_blocks`. /chat/messages must
    still rehydrate parse cards for those by mining propose_* tool calls
    out of `chat_turn_trace`. Simulated here by overwriting the assistant
    row's content_blocks to drop the tameru_proposal block after the
    persist completed — that's the post-condition for any legacy turn.
    """
    _install_scripted_anthropic(
        monkeypatch,
        [
            _MockMessage(
                content=[
                    _tool_use(
                        "propose_transaction",
                        {
                            "merchant": "Test Roastery",
                            "amount": 6.00,
                            "date": "2026-05-13",
                            "category": "Coffee Shops",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _MockMessage(
                content=[_text("Here's the parse.")],
                stop_reason="end_turn",
            ),
        ],
    )
    resp = client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "6 coffee"},
    )
    assert resp.status_code == 200
    conversation_id = json.loads(_parse_sse(resp.content)[-1][1])["conversation_id"]

    # Strip the augmented block to mimic a legacy row.
    sb = supabase_for_user(user_a.jwt)
    rows_resp = (
        sb.table("chat_messages")
        .select("id, content_blocks, role")
        .eq("conversation_id", conversation_id)
        .execute()
    )
    assistant_row = next(r for r in (rows_resp.data or []) if r["role"] == "assistant")
    legacy_blocks = [
        b for b in assistant_row["content_blocks"] if b.get("type") != "tameru_proposal"
    ]
    sb.table("chat_messages").update({"content_blocks": legacy_blocks}).eq(
        "id", assistant_row["id"]
    ).execute()

    history = client.get(
        f"/chat/messages?conversation_id={conversation_id}",
        headers=_auth(user_a),
    )
    assert history.status_code == 200
    body = history.json()
    proposal_blocks = [
        b
        for b in body["messages"][1]["content_blocks"]
        if b.get("type") == "tameru_proposal"
    ]
    assert len(proposal_blocks) == 1, (
        "trace fallback didn't rehydrate the propose_transaction call"
    )
    assert proposal_blocks[0]["tool_name"] == "propose_transaction"
    assert proposal_blocks[0]["result"]["merchant"] == "Test Roastery"


# ---------------------------------------------------------------------------
# Conversation continuity — providing conversation_id reuses it AND replays
# history to the model.
# ---------------------------------------------------------------------------


def test_conversation_id_reuse_loads_prior_history(client, user_a, monkeypatch):
    # Turn 1 — mint a conversation_id.
    """Verify that conversation id reuse loads prior history."""
    _install_scripted_anthropic(
        monkeypatch,
        [_MockMessage(content=[_text("Got it.")], stop_reason="end_turn")],
    )
    first = client.post("/chat/turn", headers=_auth(user_a), json={"message": "remember X"})
    assert first.status_code == 200
    conversation_id = json.loads(_parse_sse(first.content)[-1][1])["conversation_id"]

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
    second_frames = _parse_sse(second.content)
    second_done = json.loads(second_frames[-1][1])
    assert second_done["conversation_id"] == conversation_id

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
# Multi-hop turn replays tool_use / tool_result on the next turn.
# chat_messages stays clean; chat_turn_trace carries the full block sequence.
# ---------------------------------------------------------------------------


def test_multi_hop_turn_replays_tool_context_on_followup(
    client, user_a, card_a, monkeypatch
):
    # Seed enough Dining for the first tool call to return a real number.
    """Verify that multi hop turn replays tool context on followup."""
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
    conversation_id = json.loads(_parse_sse(first.content)[-1][1])["conversation_id"]

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

    # Turn 2: a follow-up that depends on prior tool context.
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
    # Turn-1 contributed 4 messages; turn-2 adds the new user message.
    assert len(sent) == 5, (
        f"expected 5 messages (4 from turn-1 trace + 1 new user); got "
        f"{len(sent)}: {[m['role'] for m in sent]}"
    )
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
# Loop-cap surfaces as an SSE error frame (HTTP 200), and persists nothing.
# Day 12 swap: was HTTP 500 in Day 8; now an in-stream error frame because
# the response status is already 200 by the time the cap fires.
# ---------------------------------------------------------------------------


def test_loop_cap_returns_error_frame_and_persists_nothing(client, user_a, monkeypatch):
    # Script MAX_LOOP_ITERATIONS + 1 so the assertion in _ScriptedStreamClient
    # never fires — we want the loop's own cap to be the failure mode.
    """Verify that loop cap returns an SSE error frame with no row written."""
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
    # The stream opened, so the HTTP status is 200. The failure shows up
    # as an in-stream error frame with the structured code.
    assert resp.status_code == 200
    frames = _parse_sse(resp.content)
    error_frames = [f for f in frames if f[0] == "error"]
    assert len(error_frames) == 1, frames
    payload = json.loads(error_frames[0][1])
    assert payload["code"] == "LOOP_LIMIT"
    # No `done` frame on the failure path.
    assert all(f[0] != "done" for f in frames)

    # Critical: nothing persisted in either table for this attempt —
    # the Day 12 retry-idempotency contract.
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


# ---------------------------------------------------------------------------
# GET /chat/recap — the in-app weekly recap card (DESIGN.md §6.2 / §6.4).
# ---------------------------------------------------------------------------


def test_recap_composes_on_demand_then_serves_from_cache(client, user_a, admin_client, monkeypatch):
    """First call composes + stores the recap; the second is a cache hit (no recompose).

    `compose_digest` and the activity gate are mocked so the route is exercised
    end-to-end (auth, dedup lookup, upsert, response shaping) without a live
    Sonnet call or seeding a transaction into the session-scoped `user_a`.
    """
    from datetime import datetime, timezone
    from decimal import Decimal
    from uuid import UUID

    import app.routes.chat as chat_module
    from app.services.digest import CategoryRollup, DigestPayload, SonnetCallLog

    payload = DigestPayload(
        user_id=UUID(user_a.id),
        week_start=datetime(2026, 6, 22, tzinfo=timezone.utc),
        week_end=datetime(2026, 6, 28, 23, 59, 59, tzinfo=timezone.utc),
        week_total=Decimal("180"),
        baseline_avg=Decimal("200"),
        top_category=CategoryRollup(
            category="Dining", week_total=Decimal("80"), baseline_avg=Decimal("60")
        ),
        home_currency="USD",
        observation="Spending was steady this week.",
        nudge=None,
        ui_language="en",
    )
    call_log = SonnetCallLog(
        input_tokens=10, output_tokens=5, latency_ms=100, success=True, error_code=None
    )
    calls = {"compose": 0}

    def fake_compose(_client, _user_id):
        """Count composes so the cache-hit assertion is exact."""
        calls["compose"] += 1
        return payload, call_log

    monkeypatch.setattr(chat_module, "compose_digest", fake_compose)
    monkeypatch.setattr(chat_module, "_has_recap_activity", lambda *a, **k: True)
    monkeypatch.setattr(chat_module, "_log_recap_ai_call", lambda *a, **k: None)
    # Clear any recap left by a prior run so this test starts from a miss.
    admin_client.table("weekly_recap").delete().eq("user_id", user_a.id).execute()

    try:
        first = client.get("/chat/recap", headers=_auth(user_a))
        assert first.status_code == 200, first.text
        body = first.json()
        assert body is not None
        assert body["observation"] == "Spending was steady this week."
        assert body["week_total"] == "180"
        assert body["top_category"] == "Dining"
        assert body["top_category_total"] == "80"
        assert calls["compose"] == 1

        # Second call is served from the stored row — no recompose.
        second = client.get("/chat/recap", headers=_auth(user_a))
        assert second.status_code == 200, second.text
        assert second.json()["observation"] == "Spending was steady this week."
        assert calls["compose"] == 1
    finally:
        admin_client.table("weekly_recap").delete().eq("user_id", user_a.id).execute()


def test_recap_returns_null_when_no_recent_activity(client, user_a, admin_client, monkeypatch):
    """A user with no recent activity gets no card and no Sonnet call."""
    import app.routes.chat as chat_module

    calls = {"compose": 0}

    def fake_compose(_client, _user_id):
        """Should never be reached when the activity gate returns False."""
        calls["compose"] += 1
        raise AssertionError("compose_digest called despite no recent activity")

    monkeypatch.setattr(chat_module, "compose_digest", fake_compose)
    monkeypatch.setattr(chat_module, "_has_recap_activity", lambda *a, **k: False)
    admin_client.table("weekly_recap").delete().eq("user_id", user_a.id).execute()

    resp = client.get("/chat/recap", headers=_auth(user_a))
    assert resp.status_code == 200, resp.text
    assert resp.json() is None
    assert calls["compose"] == 0


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

def _parse_sse(body: bytes) -> list[tuple[str, str]]:
    """Parse an SSE response body into `[(event, data), ...]` tuples.

    Multi-line `data:` fields are re-joined with `\\n`. Frames without an
    explicit `event:` are tagged as `"message"` (the SSE default), though
    the route always sets one.
    """
    frames: list[tuple[str, str]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for raw_line in body.decode("utf-8").split("\n"):
        line = raw_line.rstrip("\r")
        if line == "":
            if current_event is not None or current_data:
                frames.append((current_event or "message", "\n".join(current_data)))
            current_event = None
            current_data = []
        elif line.startswith("event:"):
            current_event = line[len("event:"):].lstrip(" ")
        elif line.startswith("data:"):
            current_data.append(line[len("data:"):].lstrip(" "))
    if current_event is not None or current_data:
        frames.append((current_event or "message", "\n".join(current_data)))
    return frames


@pytest.fixture(autouse=True)
def _set_anthropic_api_key(monkeypatch):
    """Loop's lazy client init checks ANTHROPIC_API_KEY even though we
    monkeypatch the client. Set a dummy and reset the cached client so
    a prior test's mock doesn't leak."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-real")
    monkeypatch.setattr(loop_module, "_client", None)

def _install_scripted_anthropic(monkeypatch, responses: list[_MockMessage]) -> _ScriptedStreamClient:
    """Support install scripted anthropic — installs the streaming mock."""
    scripted = _ScriptedStreamClient(responses)
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: scripted)
    return scripted

def _auth(user) -> dict[str, str]:
    """Support auth."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }

def _seed_transaction(
    user, *, card_id: str, merchant: str, amount: str, category: str = "Dining"
) -> str:
    """Support seed transaction."""
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
    """Support chat rows."""
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
    """Support trace rows."""
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
