"""Day 12 — SSE-specific tests beyond the route happy path.

`tests/routes/test_chat.py` covers the happy path + the loop-cap error
path. This file covers the three remaining stream-only contracts:

  1. `UCAP_EXCEEDED` surfaces as an SSE `error` frame (HTTP 200), no
     row in either table, no Anthropic call fired.
  2. `PROVIDER_RATE_LIMITED` (Anthropic 429 twice in a row) surfaces as
     an SSE `error` frame (HTTP 200), no row in either table. The
     middleware still wrote `ai_call_log` rows for the two attempts
     because Day 8's "one row per Anthropic call" invariant holds even
     on the retry path — verified here so a future refactor doesn't
     silently lose the audit trail.
  3. Multi-iteration text streams as tokens: when the loop runs two
     iterations (iter-1 text + tool_use, iter-2 final text), tokens
     from BOTH iterations arrive on the wire. This is the Day 12
     design call — iteration-1 narration flows into the same chat
     bubble as the final answer to avoid dead air during multi-hop
     turns.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import uuid
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import loop as loop_module
from app.db import supabase_for_user
from app.main import app


# ---------------------------------------------------------------------------
# Reuse the streaming-mock primitives from tests/routes/test_chat.py.
# Kept local so this file is self-contained.
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
    """Represent ScriptedStream — context manager mirroring messages.stream()."""
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
        """Yield text + content_block_stop events derived from the final message."""
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
    """Represent ScriptedStreamClient — drop-in for the loop's _anthropic_client()."""
    def __init__(self, responses: list[_MockMessage]):
        """Support the instance."""
        self._responses = list(responses)
        self.call_count = 0
        self.recorded_calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            """Represent Messages."""
            def stream(self, **kwargs: Any) -> _ScriptedStream:
                """Provide stream."""
                outer.call_count += 1
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
def http_client() -> TestClient:
    """Provide TestClient bound to the FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. UCAP_EXCEEDED → SSE error frame, no rows.
# ---------------------------------------------------------------------------


def test_usage_cap_exceeded_surfaces_as_sse_error_frame(http_client, user_a, admin_client, monkeypatch):
    """Verify that hitting the daily token cap before a turn produces an
    SSE error frame with code UCAP_EXCEEDED, HTTP 200, and no DB rows."""
    # Drop the cap so a single seeded row pushes us over.
    monkeypatch.setenv("CHAT_USAGE_CAP_TOKENS_PER_DAY", "100")

    # Wipe ai_call_log so prior tests don't contaminate today's bucket.
    today = _dt.datetime.now(_dt.timezone.utc).date()
    midnight = _dt.datetime.combine(today, _dt.time.min, tzinfo=_dt.timezone.utc)
    admin_client.table("ai_call_log").delete().eq("user_id", user_a.id).gte(
        "timestamp", midnight.isoformat()
    ).execute()

    # Seed enough chat_turn tokens to push the user over the 100-token cap.
    sb = supabase_for_user(user_a.jwt)
    sb.table("ai_call_log").insert({
        "user_id": user_a.id,
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "task_type": "chat_turn",
        "prompt_version": "chat_v2",
        "prompt_hash": "x" * 64,
        "input_tokens": 200,
        "output_tokens": 200,
        "latency_ms": 1,
        "success": True,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }).execute()

    # Install a mock that BLOWS UP if the loop reaches Anthropic — the cap
    # check should short-circuit before any stream() call fires.
    class _Boom:
        """Represent Boom."""
        class messages:
            """Represent messages."""
            @staticmethod
            def stream(**_):
                """Provide stream."""
                raise AssertionError(
                    "loop opened an Anthropic stream despite the cap check"
                )
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: _Boom())

    resp = http_client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "should be blocked"},
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.content)
    error_frames = [f for f in frames if f[0] == "error"]
    assert len(error_frames) == 1, frames
    payload = json.loads(error_frames[0][1])
    assert payload["code"] == "DAILY_CAP_EXCEEDED"
    assert all(f[0] != "done" for f in frames)

    # No row written for this attempt — retry idempotency invariant.
    user_rows = (
        sb.table("chat_messages")
        .select("id, content_blocks")
        .eq("role", "user")
        .execute()
        .data
        or []
    )
    assert not any(
        any((b.get("text") or "") == "should be blocked" for b in r["content_blocks"])
        for r in user_rows
    )


# ---------------------------------------------------------------------------
# 2. PROVIDER_RATE_LIMITED → SSE error frame, no rows, 2 ai_call_log rows.
# ---------------------------------------------------------------------------


def test_provider_rate_limited_surfaces_as_sse_error_and_logs_both_attempts(
    http_client, user_a, admin_client, monkeypatch
):
    """Verify that two consecutive Anthropic 429s land an SSE error frame
    with code PROVIDER_RATE_LIMITED, no chat rows, AND two ai_call_log
    rows (one per attempt — the Day 8 invariant survives Day 12)."""
    # Wipe ai_call_log so the attempt count is unambiguous.
    today = _dt.datetime.now(_dt.timezone.utc).date()
    midnight = _dt.datetime.combine(today, _dt.time.min, tzinfo=_dt.timezone.utc)
    admin_client.table("ai_call_log").delete().eq("user_id", user_a.id).gte(
        "timestamp", midnight.isoformat()
    ).execute()

    # Mock that raises RateLimitError on every stream() call.
    class _Always429:
        """Represent Always429 — every stream() call raises RateLimitError."""
        def __init__(self):
            """Support the instance."""
            self.attempts = 0
            outer = self

            class _Messages:
                """Represent messages."""
                @staticmethod
                def stream(**_):
                    """Always raise RateLimitError."""
                    outer.attempts += 1
                    # The SDK constructor needs a response object.
                    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
                    response = httpx.Response(429, request=request)
                    raise anthropic.RateLimitError(
                        "rate-limited",
                        response=response,
                        body=None,
                    )

            self.messages = _Messages()

    boom = _Always429()
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: boom)
    # Make the 2s retry sleep instant so the test doesn't drag.
    monkeypatch.setattr(loop_module.time, "sleep", lambda _s: None)

    resp = http_client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "stuck behind a 429"},
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.content)
    error_frames = [f for f in frames if f[0] == "error"]
    assert len(error_frames) == 1
    payload = json.loads(error_frames[0][1])
    assert payload["code"] == "AI_PROVIDER_RATE_LIMITED"
    assert all(f[0] != "done" for f in frames)

    # Two Anthropic attempts fired (initial + retry).
    assert boom.attempts == 2

    # Two ai_call_log rows for this turn — Day 8 invariant, preserved.
    sb = supabase_for_user(user_a.jwt)
    log_rows = (
        sb.table("ai_call_log")
        .select("success, error_code, task_type")
        .eq("user_id", user_a.id)
        .gte("timestamp", midnight.isoformat())
        .execute()
        .data
        or []
    )
    chat_log_rows = [r for r in log_rows if r["task_type"] == "chat_turn"]
    assert len(chat_log_rows) == 2, chat_log_rows
    assert all(r["success"] is False for r in chat_log_rows)
    assert all(r["error_code"] == "RateLimitError" for r in chat_log_rows)

    # No chat rows on the failure path.
    user_rows = (
        sb.table("chat_messages")
        .select("id, content_blocks")
        .eq("role", "user")
        .execute()
        .data
        or []
    )
    assert not any(
        any((b.get("text") or "") == "stuck behind a 429" for b in r["content_blocks"])
        for r in user_rows
    )


# ---------------------------------------------------------------------------
# 3. Multi-iteration text streams as tokens — iter-1 narration is visible.
# ---------------------------------------------------------------------------


def test_text_streams_from_every_iteration_not_just_the_final(
    http_client, user_a, card_a, monkeypatch
):
    """A two-hop turn where iteration 1 emits BOTH text and a tool_use
    block must stream both text deltas — the iteration-1 text and the
    iteration-2 text. Day 12 design call: iteration-1 narration flows
    into the same chat bubble as the final answer to avoid 4-6s of
    dead air while tools run."""
    # Seed a real transaction so the tool returns a sane value.
    sb = supabase_for_user(user_a.jwt)
    sb.table("transactions").insert({
        "user_id": user_a.id,
        "card_id": card_a,
        "merchant": f"Iter1-{uuid.uuid4().hex[:6]}",
        "amount": "12.00",
        "date": "2026-04-01",
        "category": "Dining",
        "source": "manual",
        "client_request_id": str(uuid.uuid4()),
    }).execute()

    scripted = _ScriptedStreamClient([
        _MockMessage(
            content=[
                # Iter-1 narration BEFORE the tool_use. Anthropic does this in
                # practice — "let me look that up" then the call.
                _Block(type="text", text="let me look that up… "),
                _Block(
                    type="tool_use",
                    id=f"toolu_{uuid.uuid4().hex[:8]}",
                    name="calculate_total",
                    input={"category": "Dining"},
                ),
            ],
            stop_reason="tool_use",
        ),
        _MockMessage(
            content=[_Block(type="text", text="you spent $12 on dining.")],
            stop_reason="end_turn",
        ),
    ])
    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: scripted)

    resp = http_client.post(
        "/chat/turn",
        headers=_auth(user_a),
        json={"message": "how much on dining?"},
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.content)

    # Both iterations' text must show up in the token stream.
    tokens = "".join(f[1] for f in frames if f[0] == "token")
    assert "let me look that up" in tokens, (
        f"iteration-1 narration was suppressed: tokens={tokens!r}"
    )
    assert "you spent $12 on dining." in tokens

    # And the tool_use frame fires between the two text bursts.
    event_order = [f[0] for f in frames]
    first_token_idx = event_order.index("token")
    tool_use_idx = event_order.index("tool_use")
    done_idx = event_order.index("done")
    assert first_token_idx < tool_use_idx < done_idx, (
        f"expected token→tool_use→done ordering; got {event_order}"
    )

    # Hydration parity (Codex P2 fix): chat_messages.content_blocks for the
    # assistant row must carry the FULL streamed text — iter-1 narration
    # plus iter-2 final prose. If we only persisted the final iteration's
    # blocks (the pre-fix behavior), a refresh would silently drop the
    # "let me look that up…" text the user saw stream live.
    done_payload = json.loads(frames[-1][1])
    conversation_id = done_payload["conversation_id"]
    assistant_rows = (
        sb.table("chat_messages")
        .select("role, content_blocks")
        .eq("conversation_id", conversation_id)
        .eq("role", "assistant")
        .execute()
        .data
        or []
    )
    assert len(assistant_rows) == 1, assistant_rows
    hydrated_text = "".join(
        b.get("text", "")
        for b in assistant_rows[0]["content_blocks"]
        if b.get("type") == "text"
    )
    assert "let me look that up" in hydrated_text, (
        f"chat_messages dropped iter-1 narration on persist: "
        f"hydrated_text={hydrated_text!r}"
    )
    assert "you spent $12 on dining." in hydrated_text
    # The persisted bubble must match what streamed live.
    assert hydrated_text == tokens, (
        f"hydration drifted from live stream:\n  live={tokens!r}\n  hydrated={hydrated_text!r}"
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_anthropic_api_key(monkeypatch):
    """Loop's lazy client init checks ANTHROPIC_API_KEY; set a dummy and
    reset the cached client so a prior test's mock doesn't leak."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-real")
    monkeypatch.setattr(loop_module, "_client", None)


def _auth(user) -> dict[str, str]:
    """Auth headers helper."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _parse_sse(body: bytes) -> list[tuple[str, str]]:
    """Parse an SSE response body into `[(event, data), ...]` tuples.

    Mirrors the helper in tests/routes/test_chat.py — duplicated to keep
    this file self-contained.
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
