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
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.loop import _clean_block_dict, stream_turn
from app.agent.memory import (
    MIN_CONVERSATION_MESSAGES,
    REDISTILL_DELTA,
    distill_session,
)
from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.integrations.aicalllog import log_ai_call
from app.services.digest import (
    DEFAULT_DIGEST_TZ_NAME,
    SONNET_PROMPT_VERSION,
    compose_digest,
    digest_model,
    local_week_monday,
    recap_row,
    sonnet_prompt_hash,
)

logger = logging.getLogger(__name__)

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
    background_tasks: BackgroundTasks,
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

    # Day 16 piggyback: schedule distillation of the most recently idle
    # undistilled conversation, if any. The check is the single SQL
    # predicate inside `find_idle_undistilled_conversation`; failure is
    # non-fatal — chat must not 500 because a memory side-effect didn't
    # set up. The BackgroundTask runs after the SSE stream closes; the
    # JWT closure stays valid for the seconds it takes Haiku to respond.
    _schedule_idle_distillation(
        background_tasks=background_tasks,
        user=user,
        current_conversation_id=conversation_id,
    )

    # T3 (2026-07-03): also (re-)distill the conversation the user is
    # actively in, so distillation no longer requires a return-visit after
    # the 10-minute idle window — the case that left one-sitting testers
    # with zero facts. Only for a *continuing* conversation: a brand-new
    # one (no `conversation_id` in the body) has no committed messages yet,
    # so the probe would always return nothing.
    if body.conversation_id is not None:
        _schedule_current_distillation(
            background_tasks=background_tasks,
            user=user,
            conversation_id=conversation_id,
        )

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
                        tool_calls=payload.get("tool_calls") or [],
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
    localStorage but `chatStore.messages` is empty (Day 10b §3). Response
    is the `chat_messages` row content_blocks (text + tameru_proposal
    blocks for parse-card rehydrate, Day 14b) plus per-block
    `committed_id` + `committed_state` annotations for already-confirmed
    proposals so the UI renders them in the "logged." or "deleted." badge
    state (DESIGN.md §8 status-column doctrine; see
    `_annotate_committed_proposals` below).

    Trace fallback: for assistant rows whose chat_messages content_blocks
    don't already carry `tameru_proposal` blocks (Day-12-and-earlier data
    persisted before Day 14b's `_persist_turn` augmentation, or any row
    where the embed was skipped), the corresponding `chat_turn_trace` row
    is mined for propose_* tool_use+tool_result pairs and those become
    synthetic tameru_proposal blocks on the response. The trace is the
    durable source of truth (DESIGN.md §8.12); this fallback means old
    conversations don't lose their parse cards forever just because the
    persist-time augmentation didn't exist yet.

    Committed-state detection: client_request_id from each transaction
    proposal is joined against `transactions` (base table, RLS-scoped —
    deliberately bypassing `active_transactions` so soft-deleted rows are
    visible here). A hit means the user already tapped "looks right;"
    `committed_state` carries the row's current `status` so the UI flips
    ParseCard into `logged.` (active row) or `deleted.` (deleted row),
    and the rehydrated card stays read-only either way. For card
    proposals we match by `name` across any status (cards lack an
    idempotency key — see DESIGN.md §8.1; name-uniqueness within a
    user's wallet is a best-effort proxy that's fine for v1's ~10-card
    cap).

    Capped at MESSAGES_PAGE_LIMIT recent rows. `has_more=true` tells the
    UI there's older history (no pagination cursor in v1 — the user is
    expected to start a new conversation, not paginate backwards).

    RLS: read scoped via the user's JWT against `chat_messages_owner`
    and `chat_turn_trace_owner`. The transactions/cards lookups for
    committed-state also flow through the JWT-scoped client.
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

    _inject_proposals_from_trace(client, conversation_id, rows)
    _annotate_committed_proposals(client, rows)

    messages = [
        ChatMessageResponse(
            role=row["role"],
            content_blocks=row["content_blocks"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return ChatMessagesResponse(messages=messages, has_more=has_more)


class WeeklyRecapResponse(BaseModel):
    """The in-app "This week" recap card payload (DESIGN.md §6.2 / §6.4).

    The composed weekly-digest aggregates + Sonnet narrative, surfaced as a
    pinned card at the top of the chat screen instead of only in the email
    digest. `dedup_week` (the recipient's local Monday) is the client's
    localStorage seen-key; `week_total`/`baseline_avg` drive the headline
    delta with its §6.3 color; `top_category*` drive the "X led at $Y" line;
    `observation`/`nudge` are the Sonnet prose (in `ui_language`).
    """

    model_config = ConfigDict(extra="forbid")

    dedup_week: str
    week_start: str
    week_end: str
    week_total: Decimal
    baseline_avg: Decimal
    top_category: str | None = None
    top_category_total: Decimal | None = None
    top_category_baseline: Decimal | None = None
    home_currency: str
    ui_language: str | None = None
    observation: str
    nudge: str | None = None


@router.get("/recap", response_model=WeeklyRecapResponse | None)
def chat_recap(
    user: AuthedUser = Depends(get_current_user_with_device),
) -> WeeklyRecapResponse | None:
    """Return this week's recap for the pinned chat card, or None.

    Caller: the chat page mount fetches this to render the "This week" card
    above the message thread (the card is NOT a `chat_messages` row — the
    thread stays append-only; DESIGN.md §6.2, memory 2026-05-17).

    Resolution order:
      1. A stored `weekly_recap` row for the current local week (written by
         the digest cron for digest-enabled users, or by a prior call here)
         is returned as-is — no compose, no Sonnet call. The common path.
      2. Otherwise, if the user has no recent activity worth summarizing
         (brand-new or dormant), return None — no card, no wasted Sonnet call.
      3. Otherwise compose on demand under the user's JWT (covers
         digest-disabled users), log the Sonnet call to `ai_call_log`
         (invariant 14, `task_type='recap'`), upsert the row (ON CONFLICT DO
         NOTHING — first writer for the week wins), and return it.

    RLS: everything runs under the user's JWT — `weekly_recap` read/insert and
    the `compose_digest` reads are all `auth.uid()`-scoped. No service role.
    """
    client = supabase_for_user(user.jwt)
    dedup_week = local_week_monday(
        _recap_timezone(client, user.user_id), datetime.now(timezone.utc)
    )

    existing = (
        client.table("weekly_recap")
        .select("*")
        .eq("user_id", str(user.user_id))
        .eq("dedup_week", dedup_week.isoformat())
        .limit(1)
        .execute()
    )
    if existing.data:
        return _recap_response(existing.data[0])

    if not _has_recap_activity(client, user.user_id, dedup_week):
        return None

    # On-demand compose for users without a stored recap (digest-disabled, or
    # before Monday's cron fires). The UNIQUE (user_id, dedup_week) + ON
    # CONFLICT DO NOTHING guarantees exactly one stored row; the single-active-
    # device invariant (#5) makes a concurrent second compose here effectively
    # impossible, so the wasted-Sonnet-call race is an accepted, bounded
    # tradeoff rather than something worth a lock at v1 scale.
    payload, call_log = compose_digest(client, user.user_id)
    _log_recap_ai_call(user, call_log)
    row = recap_row(payload, dedup_week)
    client.table("weekly_recap").upsert(
        row, on_conflict="user_id,dedup_week", ignore_duplicates=True
    ).execute()
    return _recap_response(row)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _recap_timezone(client, user_id: UUID) -> ZoneInfo:
    """Resolve the user's IANA timezone for the recap dedup-week computation.

    Mirrors the digest cron's fallback (memory 2026-06-01): NULL or an
    unresolvable stored zone falls back to the default digest zone, so the
    week key is always computable. Read under the caller's JWT (RLS-scoped).
    """
    resp = (
        client.table("users_meta")
        .select("timezone")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    name = resp.data[0].get("timezone") if resp.data else None
    try:
        return ZoneInfo(name) if name else ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
    except Exception:
        return ZoneInfo(DEFAULT_DIGEST_TZ_NAME)


def _has_recap_activity(client, user_id: UUID, dedup_week) -> bool:
    """True iff the user has any active transaction in the recap's lookback window.

    Gates the on-demand compose so a brand-new or dormant user (no history in
    the ~9-week window the recap summarizes) gets no card and no Sonnet call,
    rather than an empty "$0, first week" recap. The window is
    `[dedup_week - 63 days, dedup_week - 1 day]` — the baseline span through
    last Sunday. Deliberately spans the whole baseline, not just the prior
    week: an established user whose *last* week was $0 should still get a
    (positive) recap; only the truly-dormant case is suppressed.
    """
    window_start = (dedup_week - timedelta(days=63)).isoformat()
    window_end = (dedup_week - timedelta(days=1)).isoformat()
    resp = (
        client.table("transactions")
        .select("id")
        .eq("user_id", str(user_id))
        .eq("status", "active")
        .gte("date", window_start)
        .lte("date", window_end)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def _log_recap_ai_call(user: AuthedUser, call_log) -> None:
    """Write the on-demand recap's Sonnet call to `ai_call_log` under the user JWT.

    Invariant 14 — the request-handler AI-call path uses the user's JWT (not
    the service role the cron uses). `task_type='recap'` keeps on-demand
    composes distinct from scheduled `digest` sends in the cost dashboard.
    Best-effort: a failed audit write must not fail the recap response.
    """
    try:
        log_ai_call(
            user.jwt,
            user_id=user.user_id,
            provider="anthropic",
            model=digest_model(),
            task_type="recap",
            prompt_version=SONNET_PROMPT_VERSION,
            prompt_hash=sonnet_prompt_hash(),
            input_tokens=call_log.input_tokens,
            output_tokens=call_log.output_tokens,
            latency_ms=call_log.latency_ms,
            success=call_log.success,
            error_code=call_log.error_code,
        )
    except Exception:
        logger.exception(
            "recap ai_call_log write failed", extra={"user_id": str(user.user_id)}
        )


def _recap_response(row: dict[str, Any]) -> WeeklyRecapResponse:
    """Build a `WeeklyRecapResponse` from a `weekly_recap` row (or `recap_row` dict).

    One builder for both the stored-row read path and the freshly-composed
    path, since `recap_row` produces the same shape as a DB row — so a cached
    recap and a just-composed one render byte-identically. Numerics are coerced
    through `Decimal` (never float — invariant 13).
    """
    top_total = row.get("top_category_total")
    top_baseline = row.get("top_category_baseline")
    return WeeklyRecapResponse(
        dedup_week=str(row["dedup_week"]),
        week_start=str(row["week_start"]),
        week_end=str(row["week_end"]),
        week_total=Decimal(str(row["week_total"])),
        baseline_avg=Decimal(str(row["baseline_avg"])),
        top_category=row.get("top_category"),
        top_category_total=Decimal(str(top_total)) if top_total is not None else None,
        top_category_baseline=(
            Decimal(str(top_baseline)) if top_baseline is not None else None
        ),
        home_currency=row["home_currency"],
        ui_language=row.get("ui_language"),
        observation=row["observation"],
        nudge=row.get("nudge"),
    )


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
        for msg in row["messages"]:
            # Stale rows persisted before the Day 12 `parsed_output` scrub
            # may carry streaming-only fields on text blocks that Anthropic
            # 400s on inbound. Clean every block as we hydrate so existing
            # conversations don't stay wedged forever after the fix lands.
            content = msg.get("content")
            if isinstance(content, list):
                msg["content"] = [
                    _clean_block_dict(b) if isinstance(b, dict) else b
                    for b in content
                ]
            history.append(msg)
    return history

def _persist_turn(
    *,
    user: AuthedUser,
    conversation_id: UUID,
    user_message: str,
    turn_messages: list[dict[str, Any]],
    assistant_blocks: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
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

    Proposal augmentation: for any `propose_transaction` / `propose_card`
    tool call in the turn, append a synthetic `tameru_proposal` block to
    the assistant's `content_blocks` carrying the tool name + input args +
    proposal payload. This lets `/chat/messages` rehydrate parse cards on
    page refresh (the prose-only persistence behavior pre-Day-14b orphaned
    "here's the parse — tap looks right" text without a card to tap).
    The block type is Tameru-private (Anthropic's API never sees it; it's
    not in `_load_history`'s replay path), so adding fields here doesn't
    risk a 400 on the next chat turn.

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

    augmented_blocks = list(assistant_blocks)
    for tc in tool_calls:
        name = tc.get("name")
        if name not in (
            "propose_transaction",
            "propose_card",
            "propose_subscription",
        ):
            continue
        result = tc.get("result")
        # Skip is_error tool results — _renderTurn on the client never
        # surfaces them as parse cards either; the model's prose already
        # acknowledged the failure to the user.
        if not isinstance(result, dict) or "error" in result:
            continue
        augmented_blocks.append({
            "type": "tameru_proposal",
            "tool_name": name,
            "input": tc.get("input") or {},
            "result": result,
        })

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
            "content_blocks": augmented_blocks,
        },
    ]).execute()

def _inject_proposals_from_trace(
    client: Any, conversation_id: UUID, rows: list[dict[str, Any]]
) -> None:
    """Backfill `tameru_proposal` blocks on assistant rows that lack them.

    Day 14b started embedding proposal payloads on the assistant
    `chat_messages.content_blocks` at persist time so /chat/messages can
    rehydrate parse cards directly. For rows persisted earlier (or any
    row where the embed got skipped), the proposal lives only in
    `chat_turn_trace.messages` — this helper mines that trace and stitches
    the synthetic blocks back onto the matching assistant row in place.

    Pairing: chat_messages has 2 rows per turn (user + assistant) in seq
    order; chat_turn_trace has 1 row per turn in seq order. The Nth
    assistant chat_message corresponds to the Nth trace row in the
    in-memory list. Anything that breaks that 1:1 (a deleted row, a
    partially-persisted turn from a PERSISTENCE_FAILED branch) drops the
    fallback for that turn rather than misaligning everything after — the
    user sees the prose without a card, which matches the pre-fix UX and
    is preferable to silently re-pairing the wrong proposal onto the
    wrong message.

    Idempotent: rows that already have a `tameru_proposal` block are left
    alone, so re-running this on already-augmented data is a no-op.
    """
    assistant_rows = [r for r in rows if r.get("role") == "assistant"]
    if not assistant_rows:
        return

    # Only fetch trace data if at least one assistant row is missing the
    # embedded blocks — every modern turn carries them, so the typical
    # call should skip the second query entirely.
    needs_fallback = [
        r
        for r in assistant_rows
        if not any(
            isinstance(b, dict) and b.get("type") == "tameru_proposal"
            for b in (r.get("content_blocks") or [])
        )
    ]
    if not needs_fallback:
        return

    trace_resp = (
        client.table("chat_turn_trace")
        .select("messages, seq")
        .eq("conversation_id", str(conversation_id))
        .order("seq")
        .execute()
    )
    trace_rows = trace_resp.data or []
    if len(trace_rows) != len(assistant_rows):
        # Pairing assumption violated — abort the fallback rather than
        # risk attaching a proposal to the wrong turn. The user gets the
        # pre-fix UX (orphaned prose) on this conversation; the next
        # turn's persistence will land properly augmented blocks.
        return

    for assistant_row, trace_row in zip(assistant_rows, trace_rows):
        existing = assistant_row.get("content_blocks") or []
        if any(
            isinstance(b, dict) and b.get("type") == "tameru_proposal"
            for b in existing
        ):
            continue
        proposals = _extract_proposals_from_trace(trace_row.get("messages") or [])
        if proposals:
            assistant_row["content_blocks"] = list(existing) + proposals


def _extract_proposals_from_trace(
    trace_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find propose_* tool_use+tool_result pairs in one trace row.

    The trace stores the Anthropic wire-shape message list — assistant
    messages with `tool_use` blocks, then user messages with matching
    `tool_result` blocks (paired by `tool_use_id`). This helper walks the
    list, builds a result lookup, and emits one synthetic tameru_proposal
    dict per matched propose_transaction/propose_card pair.

    Tool results in the trace are JSON-encoded strings on
    `tool_result.content` (that's how Anthropic expects them on the wire);
    we json.loads them so the synthetic block carries the parsed dict the
    frontend can render without re-parsing. Errors and is_error results
    are skipped — the frontend never renders them as parse cards either.
    """
    results_by_id: dict[str, dict[str, Any]] = {}
    for msg in trace_messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            if block.get("is_error"):
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            raw = block.get("content")
            parsed: Any
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
            else:
                parsed = raw
            if not isinstance(parsed, dict) or "error" in parsed:
                continue
            results_by_id[tool_use_id] = parsed

    out: list[dict[str, Any]] = []
    for msg in trace_messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name not in (
                "propose_transaction",
                "propose_card",
                "propose_subscription",
            ):
                continue
            tool_use_id = block.get("id")
            result = results_by_id.get(tool_use_id) if isinstance(tool_use_id, str) else None
            if result is None:
                continue
            out.append({
                "type": "tameru_proposal",
                "tool_name": name,
                "input": block.get("input") or {},
                "result": result,
            })
    return out


def _annotate_committed_proposals(
    client: Any, rows: list[dict[str, Any]]
) -> None:
    """Attach `committed_id`, `committed_state`, and `committed_payload`.

    Walks the in-memory rows, collects every proposal's identifier
    (client_request_id for transactions, name for cards), runs a single
    RLS-scoped lookup against the base `transactions` / `cards` tables —
    NOT the `active_transactions` view — so soft-deleted rows are visible
    here. Mutates each block's dict in place:

      * `committed_id`      — the matched row's UUID (when any row matched).
      * `committed_state`   — `"active"` or `"deleted"` carried back from
        the row's `status` column. The frontend's ParseCard switches on
        this to render `logged.` vs `deleted.` badges; the rehydrated
        card is always read-only when `committed_id` is set, regardless
        of state.
      * `committed_payload` — the *current* values of the user-editable
        fields on the matched row. Day 15 addition: the original
        `input`/`result` blocks freeze the agent's proposal, but the user
        may have edited the parse card before tapping "looks right" (and
        may have edited the row again later via the edit sheet). Without
        `committed_payload`, a rehydrated `logged.` card would display
        the agent's original number even after an edit. The frontend
        `_proposalToDraft` prefers this over `result` when present.

    Reading from the base table (with RLS) is the load-bearing distinction
    from default app reads: this is one of the two surfaces explicitly
    documented in DESIGN.md §8.2 as opting into the base table — the chat
    rehydrate annotation needs to distinguish "never confirmed" from
    "confirmed and deleted" to set the badge correctly, and a `deleted.`
    badge with stale display values is still wrong.

    Card matching is by `client_request_id` (Day 15 follow-up — see
    migration `20260517120000_cards_client_request_id.sql`). Each
    `propose_card` proposal mints a stable UUID that the row carries
    after `/cards/confirm`; the join is 1:1 even when a user holds two
    same-name cards differing on `last_four`. Legacy proposal blocks
    (predating the crid column) fall back to a name match — best-effort
    for two-same-name cards in old history, but every new proposal
    works cleanly.
    """
    crid_set: set[str] = set()
    card_crid_set: set[str] = set()
    card_name_set: set[str] = set()
    sub_crid_set: set[str] = set()
    for row in rows:
        if row.get("role") != "assistant":
            continue
        for block in row.get("content_blocks") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tameru_proposal":
                continue
            tool_name = block.get("tool_name")
            result = block.get("result") or {}
            if tool_name == "propose_transaction":
                crid = result.get("client_request_id")
                if isinstance(crid, str):
                    crid_set.add(crid)
            elif tool_name == "propose_card":
                # Prefer crid; fall back to name for legacy blocks (the
                # crid column was added Day 15; older persisted blocks
                # only carry a name).
                card_crid = result.get("client_request_id")
                if isinstance(card_crid, str) and card_crid:
                    card_crid_set.add(card_crid)
                else:
                    name = result.get("name")
                    if isinstance(name, str) and name:
                        card_name_set.add(name)
            elif tool_name == "propose_subscription":
                # Subscriptions are crid-only — propose_subscription always
                # mints a fresh UUID (Day 19) and there's no legacy
                # name-only path because the tool wasn't shipped before
                # the crid column existed.
                sub_crid = result.get("client_request_id")
                if isinstance(sub_crid, str) and sub_crid:
                    sub_crid_set.add(sub_crid)

    # `committed_txs` maps crid → full row dict. Same shape for cards
    # (keyed by `name`). The full row is kept so we can stitch
    # `committed_payload` onto each block without a second query. When the
    # same crid has both active and deleted rows the partial unique index
    # ensures only one is active — pick that one for the "is this still
    # logged?" answer. For two deleted rows, the first seen is fine.
    committed_txs: dict[str, dict[str, Any]] = {}
    if crid_set:
        tx_resp = (
            client.table("transactions")
            .select(
                "id, client_request_id, status, deleted_at, "
                "amount, merchant, date, category, card_id, notes"
            )
            .in_("client_request_id", list(crid_set))
            .execute()
        )
        for r in tx_resp.data or []:
            crid = r.get("client_request_id")
            tx_id = r.get("id")
            row_status = r.get("status") or "active"
            if not (isinstance(crid, str) and isinstance(tx_id, str)):
                continue
            prior = committed_txs.get(crid)
            # Active row wins over deleted; among same-status candidates,
            # the first seen is fine for v1 (partial-unique-index guarantees
            # at most one active per crid, and "any deleted id" suffices to
            # render the deleted badge).
            prior_status = (prior or {}).get("status") if prior else None
            if prior is None or (prior_status != "active" and row_status == "active"):
                committed_txs[crid] = r

    # `committed_cards_by_crid` is the load-bearing lookup (1:1 join);
    # `committed_cards_by_name` is the legacy fallback for proposal blocks
    # written before the crid column existed. `alias` isn't a column on
    # `cards` (DESIGN.md §8.1 — aliases are proposal-time annotations,
    # not row state); it falls through to the proposal `result` via the
    # frontend's spread merge in `_proposalToCardDraft`.
    _card_select = (
        "id, name, status, deleted_at, "
        "network, last_four, issuer, program, multipliers, "
        "annual_fee, source_urls, client_request_id, "
        # Tier-3 columns (audit P3-32): region is recomputed server-side
        # at confirm, so the live row can legitimately differ from the
        # proposal — omitting these made the chat card a stale view of
        # exactly the fields confirm can change.
        "region, base_reward_rate, rewards_currency"
    )
    committed_cards_by_crid: dict[str, dict[str, Any]] = {}
    if card_crid_set:
        card_resp = (
            client.table("cards")
            .select(_card_select)
            .in_("client_request_id", list(card_crid_set))
            .execute()
        )
        for r in card_resp.data or []:
            row_crid = r.get("client_request_id")
            card_id = r.get("id")
            row_status = r.get("status") or "active"
            if not (isinstance(row_crid, str) and isinstance(card_id, str)):
                continue
            prior = committed_cards_by_crid.get(row_crid)
            prior_status = (prior or {}).get("status") if prior else None
            if prior is None or (prior_status != "active" and row_status == "active"):
                committed_cards_by_crid[row_crid] = r

    committed_cards_by_name: dict[str, dict[str, Any]] = {}
    if card_name_set:
        card_resp = (
            client.table("cards")
            .select(_card_select)
            .in_("name", list(card_name_set))
            .execute()
        )
        for r in card_resp.data or []:
            name = r.get("name")
            card_id = r.get("id")
            row_status = r.get("status") or "active"
            if not (isinstance(name, str) and isinstance(card_id, str)):
                continue
            prior = committed_cards_by_name.get(name)
            prior_status = (prior or {}).get("status") if prior else None
            if prior is None or (prior_status != "active" and row_status == "active"):
                committed_cards_by_name[name] = r

    # Subscriptions — Day 19. Crid-only join (no legacy name-fallback
    # path because `propose_subscription` didn't ship pre-crid). Same
    # active-wins / first-deleted-wins picking logic as transactions and
    # cards. Subscriptions have three lifecycle states; for the rehydrate
    # badge we collapse the picking rule to "active or paused wins over
    # cancelled" so the parse card distinguishes `tracking.` / `paused.`
    # from `cancelled.` correctly.
    _sub_select = (
        "id, name, amount, frequency, start_date, next_billing_date, "
        "category, card_id, status, client_request_id"
    )
    committed_subs_by_crid: dict[str, dict[str, Any]] = {}
    if sub_crid_set:
        sub_resp = (
            client.table("subscriptions")
            .select(_sub_select)
            .in_("client_request_id", list(sub_crid_set))
            .execute()
        )
        for r in sub_resp.data or []:
            row_crid = r.get("client_request_id")
            sub_id = r.get("id")
            row_status = r.get("status") or "active"
            if not (isinstance(row_crid, str) and isinstance(sub_id, str)):
                continue
            prior = committed_subs_by_crid.get(row_crid)
            prior_status = (prior or {}).get("status") if prior else None
            # Non-cancelled rows win; among same-status the first wins.
            if prior is None or (
                prior_status == "cancelled" and row_status != "cancelled"
            ):
                committed_subs_by_crid[row_crid] = r

    if (
        not committed_txs
        and not committed_cards_by_crid
        and not committed_cards_by_name
        and not committed_subs_by_crid
    ):
        return

    for row in rows:
        if row.get("role") != "assistant":
            continue
        for block in row.get("content_blocks") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tameru_proposal":
                continue
            tool_name = block.get("tool_name")
            result = block.get("result") or {}
            if tool_name == "propose_transaction":
                crid = result.get("client_request_id")
                if isinstance(crid, str) and crid in committed_txs:
                    matched = committed_txs[crid]
                    block["committed_id"] = matched["id"]
                    block["committed_state"] = matched.get("status") or "active"
                    block["committed_payload"] = _tx_committed_payload(matched)
            elif tool_name == "propose_card":
                # crid wins; fall back to name for legacy blocks. The
                # crid path is the load-bearing fix for two-same-name
                # cards (e.g. "Amex Gold" 1234 vs "Amex Gold" 5678).
                card_crid = result.get("client_request_id")
                matched: dict[str, Any] | None = None
                if isinstance(card_crid, str) and card_crid in committed_cards_by_crid:
                    matched = committed_cards_by_crid[card_crid]
                else:
                    name = result.get("name")
                    if isinstance(name, str) and name in committed_cards_by_name:
                        matched = committed_cards_by_name[name]
                if matched is not None:
                    block["committed_id"] = matched["id"]
                    block["committed_state"] = matched.get("status") or "active"
                    block["committed_payload"] = _card_committed_payload(matched)
            elif tool_name == "propose_subscription":
                # Crid-only join (Day 19 shipped post-crid; no legacy
                # name fallback). Returns the row's current `status` so
                # the frontend can render `tracking.` / `paused.` /
                # `cancelled.` correctly on rehydrate.
                sub_crid = result.get("client_request_id")
                if isinstance(sub_crid, str) and sub_crid in committed_subs_by_crid:
                    matched = committed_subs_by_crid[sub_crid]
                    block["committed_id"] = matched["id"]
                    block["committed_state"] = matched.get("status") or "active"
                    block["committed_payload"] = _sub_committed_payload(matched)


def _tx_committed_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Project a transactions row into the user-editable `committed_payload`.

    Mirrors the field set the edit sheet exposes (merchant, amount, date,
    category, card_id, notes) plus the `client_request_id` so the frontend
    can sanity-check the join key didn't drift. Amount is serialized as the
    same string shape `/transactions/confirm` returns, so `_proposalToDraft`
    on the frontend can build a `ParseDraft` from `committed_payload` and
    `result` interchangeably without a separate parser branch.
    """
    return {
        "client_request_id": row.get("client_request_id"),
        "merchant": row.get("merchant"),
        "amount": row.get("amount"),
        "date": row.get("date"),
        "card_id": row.get("card_id"),
        "category": row.get("category"),
        "notes": row.get("notes"),
    }


def _sub_committed_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Project a subscriptions row into the `committed_payload` projection.

    Mirrors the fields the frontend `_proposalToSubscriptionDraft` builds
    a `SubscriptionParseDraft` from. The chat-side draft and the wire
    SubscriptionProposal share the same snake_case names, so this is a
    direct passthrough of the editable / displayed columns.
    """
    return {
        "client_request_id": row.get("client_request_id"),
        "name": row.get("name"),
        "amount": row.get("amount"),
        "frequency": row.get("frequency"),
        "start_date": row.get("start_date"),
        "next_billing_date": row.get("next_billing_date"),
        "category": row.get("category"),
        "card_id": row.get("card_id"),
    }


def _card_committed_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Project a cards row into the `committed_payload` projection.

    Mirrors the columns the `cards` table actually carries (DESIGN.md
    §8.6) so the frontend `_proposalToCardDraft` can build a
    `CardParseDraft` from `committed_payload` without a separate parser
    branch. `gemini_suggestion`, `needs_manual`, and `alias` are
    intentionally omitted — those are proposal-time annotations (or
    user-chosen labels) that don't persist as row state in v1. They
    fall through to the proposal `result` via the spread fallback on
    the frontend.
    """
    multipliers = row.get("multipliers")
    if not isinstance(multipliers, dict):
        multipliers = {}
    source_urls = row.get("source_urls")
    if not isinstance(source_urls, list):
        source_urls = []
    return {
        "client_request_id": row.get("client_request_id"),
        "network": row.get("network"),
        "last_four": row.get("last_four"),
        "name": row.get("name"),
        "issuer": row.get("issuer"),
        "program": row.get("program"),
        "multipliers": multipliers,
        "annual_fee": row.get("annual_fee"),
        "source_urls": source_urls,
        # Tier-3 fields (audit P3-32). `region` is recomputed server-side
        # at confirm time (resolve_card_region), so the committed value
        # can legitimately differ from the proposal's — the current-state
        # view (memory.md 2026-05-17) must reflect the live row.
        "region": row.get("region"),
        "base_reward_rate": row.get("base_reward_rate"),
        "rewards_currency": row.get("rewards_currency"),
    }


def _schedule_idle_distillation(
    *,
    background_tasks: BackgroundTasks,
    user: AuthedUser,
    current_conversation_id: UUID,
) -> None:
    """Probe for an idle conversation needing (re-)distillation and schedule it.

    Calls the `find_idle_undistilled_conversation` RPC under the user's JWT —
    the 10-minute idle threshold and the count-delta predicate (never
    distilled, OR grown by `REDISTILL_DELTA` since its last distillation) both
    live in that SQL. This is the *backstop* for a conversation the user has
    walked away from; the current-conversation probe handles the active one.
    If a row comes back, queue `distill_session` as a FastAPI BackgroundTask
    so it runs after the SSE stream closes.

    Any failure is logged and swallowed. The chat turn proceeds normally; the
    next turn's firing retries (the state row advances only when distillation
    succeeds end-to-end).
    """
    try:
        client = supabase_for_user(user.jwt)
        resp = client.rpc(
            "find_idle_undistilled_conversation",
            {
                "p_current_conversation_id": str(current_conversation_id),
                "p_min_messages": MIN_CONVERSATION_MESSAGES,
                "p_redistill_delta": REDISTILL_DELTA,
            },
        ).execute()
        target = resp.data
        # PostgREST returns either the scalar value or null for a scalar-
        # returning RPC. supabase-py surfaces the same shape directly.
        if not target:
            return
        if isinstance(target, list):
            target = target[0] if target else None
            if not target:
                return
        target_id = UUID(str(target))
        background_tasks.add_task(distill_session, user.jwt, target_id)
    except Exception:
        logger.exception("piggyback distillation probe failed; turn continues")


def _schedule_current_distillation(
    *,
    background_tasks: BackgroundTasks,
    user: AuthedUser,
    conversation_id: UUID,
) -> None:
    """(Re-)distill the conversation the user is actively in, as it grows.

    Complements `_schedule_idle_distillation`, which only catches a *prior*
    conversation after the 10-minute idle window. This probe fires for the
    current conversation, so distillation no longer requires a return-visit
    — `find_conversation_to_distill` returns it once it has
    `MIN_CONVERSATION_MESSAGES` committed rows and has grown by
    `REDISTILL_DELTA` since its last distillation (the message-count delta
    lives in the SQL).

    Runs before the current turn is persisted, so it sees only committed
    history; the just-sent turn is caught on the next probe or by the idle
    backstop. Any failure is logged and swallowed — the turn proceeds.
    """
    try:
        client = supabase_for_user(user.jwt)
        resp = client.rpc(
            "find_conversation_to_distill",
            {
                "p_conversation_id": str(conversation_id),
                "p_min_messages": MIN_CONVERSATION_MESSAGES,
                "p_redistill_delta": REDISTILL_DELTA,
            },
        ).execute()
        target = resp.data
        if not target:
            return
        if isinstance(target, list):
            target = target[0] if target else None
            if not target:
                return
        background_tasks.add_task(distill_session, user.jwt, UUID(str(target)))
    except Exception:
        logger.exception(
            "current-conversation distillation probe failed; turn continues"
        )


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
