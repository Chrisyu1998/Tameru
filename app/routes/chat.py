"""Chat REST endpoint — Day 8.

POST /chat/turn runs one Claude Haiku turn (one-or-more model calls in the
agent loop) against the user's transactions and persists two artifacts:

  * `chat_messages` — the human-visible conversation log. One user row +
    one assistant row per turn, both with simple text content_blocks. This
    is what Day 10's chat thread renders. UI never sees synthetic
    tool_result rows here.
  * `chat_turn_trace` — the wire-shape replay log. One row per turn,
    storing the full Anthropic message-list slice (user-typed text +
    every intermediate `assistant_with_tool_use` and `user_with_tool_result`
    pair + final assistant blocks). The loop reads from this on the next
    turn so prior tool interactions replay faithfully (DESIGN.md §8.12).

Two tables, two purposes — see DESIGN.md §8.11/§8.12 for the full
rationale. Non-streaming today; Day 12 swaps to SSE.

History cap: load the last 5 trace rows for this conversation per
DESIGN.md §7.2.1 ("last 5 turns"). With one row per turn, the cap maps
exactly regardless of hop count. Older turns will be summarized into
user_memory by Day 16; today we simply truncate.

Service role: never used here. The handler runs with the user's JWT, the
loop runs with the user's JWT, the ai_call_log writer uses the user's JWT
(CLAUDE.md invariant 14).
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agent.loop import (
    AgentLoopLimitExceeded,
    AssistantTurn,
    ToolCallRecord,
    run_turn,
)
from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user

router = APIRouter(prefix="/chat", tags=["chat"])

# Last 5 trace rows = last 5 turns regardless of how many tool hops each
# turn contained (DESIGN.md §7.2.1, §8.12). Encoding the cap from day one
# means Day 16's memory layer doesn't need to retrofit it.
HISTORY_TURN_LIMIT = 5


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: str = Field(min_length=1)


class ChatToolCallResponse(BaseModel):
    name: str
    input: dict[str, Any]
    result: dict[str, Any]


class ChatTurnResponse(BaseModel):
    conversation_id: UUID
    assistant_text: str
    tool_calls: list[ChatToolCallResponse]


def _load_history(user: AuthedUser, conversation_id: UUID) -> list[dict[str, Any]]:
    """Reconstruct the Anthropic-shaped message list from chat_turn_trace.

    Picks the last HISTORY_TURN_LIMIT trace rows (each row = one turn's
    full message slice), reverses to chronological order, and concatenates
    their `messages` arrays. The result is the exact wire-shape Claude
    needs to ground a follow-up turn — including tool_use / tool_result
    pairs from prior turns, not just the prose.

    RLS scopes the read.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("chat_turn_trace")
        .select("messages, seq")
        .eq("conversation_id", str(conversation_id))
        # seq is the unambiguous insertion-order tiebreaker — turns are
        # inherently sequential per conversation, but two turns in the
        # same microsecond are possible during testing.
        .order("seq", desc=True)
        .limit(HISTORY_TURN_LIMIT)
        .execute()
    )
    rows = list(reversed(resp.data or []))
    history: list[dict[str, Any]] = []
    for row in rows:
        history.extend(row["messages"])
    return history


def _persist_turn(
    user: AuthedUser,
    conversation_id: UUID,
    user_message: str,
    turn: AssistantTurn,
) -> None:
    """Write to both tables. Trace first — it's the load-bearing row for
    next-turn replay; if the chat_messages write fails afterward the
    conversation looks empty in the UI but the model still has correct
    context.

    Atomicity caveat unchanged from the single-table design: Supabase
    Python exposes no transaction primitive, so a partial write across
    the two tables is technically possible. v1 accepts this — the worst
    case is a brief UI/replay desync that resolves on the next turn.
    Stronger atomicity (RPC) is a Day 12+ concern when streaming makes
    persistence asynchronous.
    """
    client = supabase_for_user(user.jwt)

    # Trace row first — load-bearing for replay.
    client.table("chat_turn_trace").insert({
        "user_id": str(user.user_id),
        "conversation_id": str(conversation_id),
        "messages": turn.turn_messages,
    }).execute()

    # Human-visible rows: just the user-typed text + the assistant's
    # final-iteration blocks. Synthetic tool_result blocks live in the
    # trace, never here, so the UI thread renders cleanly without
    # filtering.
    client.table("chat_messages").insert([
        {
            "user_id": str(user.user_id),
            "conversation_id": str(conversation_id),
            "role": "user",
            "content_blocks": [{"type": "text", "text": user_message}],
        },
        {
            "user_id": str(user.user_id),
            "conversation_id": str(conversation_id),
            "role": "assistant",
            "content_blocks": turn.content_blocks,
        },
    ]).execute()


def _to_response_tool_calls(records: list[ToolCallRecord]) -> list[ChatToolCallResponse]:
    return [
        ChatToolCallResponse(name=r.name, input=r.input, result=r.result)
        for r in records
    ]


@router.post("/turn", response_model=ChatTurnResponse)
def chat_turn(
    body: ChatTurnRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> ChatTurnResponse:
    conversation_id = body.conversation_id or uuid.uuid4()

    history = _load_history(user, conversation_id) if body.conversation_id else []

    try:
        turn = run_turn(user, history, body.message)
    except AgentLoopLimitExceeded as exc:
        # Don't persist — a partial turn that hit the cap isn't a useful
        # row to keep around (the assistant text is empty / nonsensical
        # by definition). Surface as 500 with a structured code so the
        # frontend can render a specific message.
        raise HTTPException(
            status_code=500,
            detail={"code": "LOOP_LIMIT", "message": str(exc)},
        ) from exc

    _persist_turn(user, conversation_id, body.message, turn)

    return ChatTurnResponse(
        conversation_id=conversation_id,
        assistant_text=turn.assistant_text,
        tool_calls=_to_response_tool_calls(turn.tool_calls),
    )
