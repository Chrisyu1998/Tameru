"""Chat REST endpoint — Day 12 (SSE).

POST /chat/turn runs one Claude Haiku turn (one-or-more model calls in
the agent loop) against the user's transactions and persists two
artifacts on success:

  * `chat_messages` — the human-visible conversation log. One user row +
    one assistant row per turn, both with simple text content_blocks.
  * `chat_turn_trace` — the wire-shape replay log. One row per turn,
    storing the full Anthropic message-list slice. The loop reads from
    this on the next turn so prior tool interactions replay faithfully
    (DESIGN.md §8.12).

Wire mode: Server-Sent Events (Day 12, DESIGN.md §7.5). The response is
`Content-Type: text/event-stream`; four frame types — `token` (per text
delta from any iteration), `tool_use` (when a tool call is assembled),
`done` (terminal success, carries `tool_calls` in Day 8's exact shape),
`error` (terminal failure with structured code). The HTTP status is 200
once the stream opens, so failures must surface as `error` frames, not
HTTPException.

Persistence happens **after** the terminal `done` frame, in one shot.
A mid-stream drop therefore leaves zero rows in either table, so a
client-initiated retry of the same `{conversation_id, message}` runs
cleanly with `_load_history()` returning the same prior history. (Per-
iteration `ai_call_log` rows are still written for cost accounting and
are correct even if the user-visible row never lands.)

History cap: last 5 trace rows for this conversation per DESIGN.md
§7.2.1. One row per turn, so the cap maps exactly regardless of hop
count.

Service role: never used here. Handler + loop + ai_call_log writer all
run with the user's JWT (CLAUDE.md invariant 14).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.loop import stream_turn
from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user

router = APIRouter(prefix="/chat", tags=["chat"])

# Last 5 trace rows = last 5 turns regardless of how many tool hops each
# turn contained (DESIGN.md §7.2.1, §8.12). Encoding the cap from day one
# means Day 16's memory layer doesn't need to retrofit it.
HISTORY_TURN_LIMIT = 5

# Cap GET /chat/messages at the most recent 50 rows. "Recent" is what the
# user wants to see after a page refresh; deep scrollback is a Day 16+
# concern (memory + summarization layer). 50 rows = 25 turns assuming the
# 1 user + 1 assistant row pattern, which comfortably exceeds the 5-turn
# replay cap above.
MESSAGES_PAGE_LIMIT = 50


class ChatTurnRequest(BaseModel):
    """Represent ChatTurnRequest."""
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: str = Field(min_length=1)


class ChatMessageResponse(BaseModel):
    """One row of human-visible chat history.

    `content_blocks` is the raw JSONB we stored at turn time — the same
    Anthropic-shaped block list as `chat_turn` returns in `assistant_text`,
    minus the tool_use/tool_result hops which live only in `chat_turn_trace`.
    Frontend collapses these to plain text for rehydration (Day 10b §3 spec:
    parse cards / candidate lists are NOT re-rendered as interactive cards).
    """

    role: str
    content_blocks: list[dict[str, Any]]
    created_at: str


class ChatMessagesResponse(BaseModel):
    """Represent ChatMessagesResponse."""

    messages: list[ChatMessageResponse]
    has_more: bool


@router.post("/turn")
def chat_turn(
    body: ChatTurnRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> StreamingResponse:
    """Stream one chat turn as Server-Sent Events.

    Body: `{conversation_id?: UUID, message: str}`. The 422 contract from
    Day 8 still holds (Pydantic validates before the stream opens, so
    missing/empty `message` returns an HTTP 422 with a normal JSON body
    — no SSE).

    Response: `Content-Type: text/event-stream`, status 200. Frames:
      - `event: token`    data: `<chunk>`
      - `event: tool_use` data: `{"name", "input"}`
      - `event: done`     data: `{"conversation_id", "tool_calls"}`
      - `event: error`    data: `{"code", "message"}`

    The `done.tool_calls` array is byte-for-byte the same shape Day 8's
    non-streaming response returned — Day 10's UI consumes it unchanged.

    Persistence: writes to `chat_messages` + `chat_turn_trace` fire only
    after the `done` frame is yielded. A mid-stream drop or `error`
    frame leaves zero rows, which is the property that makes a
    client-initiated retry idempotent (DESIGN.md §7.5).
    """
    conversation_id = body.conversation_id or uuid.uuid4()
    history = _load_history(user, conversation_id) if body.conversation_id else []

    def generate() -> Iterator[bytes]:
        """Produce the SSE byte stream for this turn.

        Closures over `user`, `conversation_id`, `history`, `body.message`
        and the persistence helper. The Anthropic client and tool
        execution all run inside `stream_turn`; we only translate
        StreamEvents into SSE wire frames here.
        """
        for evt in stream_turn(user, history, body.message):
            if evt.kind == "token":
                yield _sse_frame("token", evt.text)
            elif evt.kind == "tool_use":
                yield _sse_frame("tool_use", json.dumps(evt.tool_use or {}))
            elif evt.kind == "done":
                payload = evt.done or {}
                # Persist BEFORE yielding `done`. If the persistence
                # write fails, we want the client to see an `error`
                # frame, not a `done` followed by the next turn finding
                # missing history. The trace row is load-bearing for
                # next-turn replay; the chat_messages rows feed the UI
                # rehydrate path.
                try:
                    _persist_turn(
                        user=user,
                        conversation_id=conversation_id,
                        user_message=body.message,
                        turn_messages=payload.get("turn_messages") or [],
                        assistant_blocks=payload.get("content_blocks") or [],
                    )
                except Exception as exc:  # noqa: BLE001 — surface anything
                    yield _sse_frame(
                        "error",
                        json.dumps({
                            "code": "PERSISTENCE_FAILED",
                            "message": str(exc),
                        }),
                    )
                    return
                yield _sse_frame(
                    "done",
                    json.dumps({
                        "conversation_id": str(conversation_id),
                        "tool_calls": payload.get("tool_calls") or [],
                    }),
                )
                return
            elif evt.kind == "error":
                # Loop surfaced a known-failure class. No persistence.
                yield _sse_frame("error", json.dumps(evt.error or {}))
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            # Day 12: tell intermediaries not to coalesce small chunks
            # into bursts (Railway edge + any reverse proxy in between).
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            # Conventional for SSE; some clients use it to detect the
            # connection style before parsing the body.
            "Connection": "keep-alive",
        },
    )


@router.get("/messages", response_model=ChatMessagesResponse)
def chat_messages(
    conversation_id: UUID = Query(...),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> ChatMessagesResponse:
    """Return human-visible history for one conversation, oldest-first.

    Caller: chat page mount, when a `tameru-chat-conversation-id` is in
    localStorage but `chatStore.messages` is empty (Day 10b §3). The wire
    shape mirrors `chat_messages` minus internal columns — `role +
    content_blocks + created_at` is the minimum the UI needs to re-render
    text bubbles. Tool-use trace rows live in `chat_turn_trace` and are
    deliberately not surfaced.

    Capped at MESSAGES_PAGE_LIMIT recent rows. `has_more=true` tells the
    UI there's older history (no pagination cursor in v1 — the user is
    expected to start a new conversation, not paginate backwards).

    RLS: read scoped via the user's JWT against `chat_messages_owner`.
    """
    client = supabase_for_user(user.jwt)
    # Fetch limit+1 to detect more rows without a separate count query.
    resp = (
        client.table("chat_messages")
        .select("role, content_blocks, created_at, seq")
        .eq("conversation_id", str(conversation_id))
        .order("seq", desc=True)
        .limit(MESSAGES_PAGE_LIMIT + 1)
        .execute()
    )
    rows = resp.data or []
    has_more = len(rows) > MESSAGES_PAGE_LIMIT
    if has_more:
        rows = rows[:MESSAGES_PAGE_LIMIT]
    # We pulled newest-first to apply the limit; flip back to chronological
    # order for the UI so the rendering code stays the simpler append-only
    # shape.
    rows.reverse()
    messages = [
        ChatMessageResponse(
            role=row["role"],
            content_blocks=row["content_blocks"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return ChatMessagesResponse(messages=messages, has_more=has_more)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

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
    *,
    user: AuthedUser,
    conversation_id: UUID,
    user_message: str,
    turn_messages: list[dict[str, Any]],
    assistant_blocks: list[dict[str, Any]],
) -> None:
    """Write the trace + human-visible rows for one completed turn.

    Called from inside the SSE generator after the loop yields its
    terminal `done` event. On `error` (or a mid-stream drop), this is
    NOT called — the dropped turn leaves no rows, which is the property
    that lets the client retry the same `{conversation_id, message}`
    cleanly (DESIGN.md §7.5).

    Trace first — it's the load-bearing row for next-turn replay; if
    the chat_messages write fails afterward the conversation looks
    empty in the UI but the model still has correct context.

    Atomicity caveat: Supabase Python exposes no transaction primitive,
    so a partial write across the two tables is technically possible.
    v1 accepts this — the worst case is a brief UI/replay desync that
    resolves on the next turn.
    """
    client = supabase_for_user(user.jwt)

    client.table("chat_turn_trace").insert({
        "user_id": str(user.user_id),
        "conversation_id": str(conversation_id),
        "messages": turn_messages,
    }).execute()

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
            "content_blocks": assistant_blocks,
        },
    ]).execute()

def _sse_frame(event: str, data: str) -> bytes:
    """Encode one SSE frame as bytes.

    SSE frame shape per the spec:
        event: <name>\\n
        data: <line 1>\\n
        data: <line 2>\\n
        \\n

    Multi-line payloads need each line prefixed with `data: `; we split
    on `\\n` to handle text deltas that contain newlines (the model can
    and does emit them). JSON payloads from json.dumps default to a
    single line, but tokens carrying user-visible prose may not.
    """
    lines = [f"event: {event}"]
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")  # trailing blank line terminates the frame
    lines.append("")
    return "\n".join(lines).encode("utf-8")
