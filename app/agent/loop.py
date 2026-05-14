"""Claude Haiku agent loop — Day 8 minimum (one tool, no streaming).

This is the loop sketched in DESIGN.md §7.1 implemented as the smallest
thing that proves the wire works: one tool (calculate_total), non-
streaming, sync. Day 9 plugs in the rest of the tool surface and the
middleware (usage cap, 429 backoff) without changing the loop's shape.
Day 12 swaps the non-streaming `messages.create()` call for
`messages.stream()` while preserving everything else.

Sync-by-design (CLAUDE.md decision documented in the Day 8 prompt).
The codebase is sync; FastAPI runs sync handlers in a threadpool. An
`async` loop here would either need `AsyncAnthropic` paired with
`run_in_threadpool` for every Supabase call (which negates the
async-event-loop benefit), or a port of `app/db.py` to async
(out-of-scope for Day 8). Revisit when threadpool saturation is a
measured problem.

Logging: one ai_call_log row per `messages.create()` call (i.e. per loop
iteration) via the user-JWT path (CLAUDE.md invariant 14). Failures are
logged with success=False before the exception propagates so the audit
trail covers the whole call, not just successes.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic import Anthropic

from app.agent.middleware import (
    ProviderRateLimited,
    UsageCapExceeded,
    assert_within_usage_cap,
)
from app.agent.tools import execute_tool, tool_schemas
from app.auth import AuthedUser
from app.integrations.aicalllog import log_ai_call
from app.prompts.chat import (
    PROMPT_VERSION,
    render_system_prompt,
    system_prompt_hash,
)

__all__ = [
    "AgentLoopError",
    "AgentLoopLimitExceeded",
    "AssistantTurn",
    "MAX_LOOP_ITERATIONS",
    "ProviderRateLimited",
    "ToolCallRecord",
    "UsageCapExceeded",
    "run_turn",
]

# Hard ceiling on loop iterations. A pathological prompt (deliberate or
# bug-induced) where Claude keeps requesting tool calls without converging
# would otherwise burn tokens and latency unboundedly. Eight is the
# DESIGN.md §7.2.1 budget — a 4-hop turn is typical, so 8 leaves headroom
# for legitimate multi-tool reasoning while still bounding worst case.
MAX_LOOP_ITERATIONS = 8

# Default model when ANTHROPIC_MODEL isn't set. Matches CLAUDE.md "Model
# usage by task" — chat agent uses Haiku 4.5. Env override exists so eval
# experiments and the planned post-launch Flash-Lite A/B (DESIGN.md §11.4)
# can swap models without a code change.
_DEFAULT_CHAT_MODEL = "claude-haiku-4-5"


class AgentLoopError(Exception):
    """Base for loop failures the route handler turns into 5xx responses."""


class AgentLoopLimitExceeded(AgentLoopError):
    """The 8-iteration safety cap fired before the model returned end_turn."""


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool_use → tool_result pair, surfaced in the route response.

    The Day 10 chat UI iterates these to render ParseCard / CandidateList
    components. `result` is the raw dict the executor returned (or an
    {"error": ...} payload if the call failed); the UI decides how to
    render based on `name`.
    """

    name: str
    input: dict[str, Any]
    result: dict[str, Any]


@dataclass
class AssistantTurn:
    """Outcome of one full chat turn.

    Three artifacts, three consumers:

      * `assistant_text` — the final-iteration prose. Goes to the chat
        bubble (Day 10 UI) and to `chat_messages` as the human-visible
        assistant row.
      * `content_blocks` — the final iteration's assistant blocks (text
        only at end_turn, but a future stop_reason could include other
        block types). Stored on `chat_messages.content_blocks` for the
        UI-side rendering.
      * `turn_messages` — the FULL Anthropic message-list slice
        contributed by this turn: `[{user-typed}, {assistant_with_tool_use},
        {user_with_tool_result}, ..., {assistant_final}]`. Persisted to
        `chat_turn_trace.messages` so the loop can faithfully replay
        prior tool interactions on the next turn (DESIGN.md §8.12).
        Without this, follow-up turns that reference prior tool output
        lose grounding — the prose alone doesn't tell Claude what tool
        was called or with what args.

    `tool_calls` is the per-iteration trace surfaced to the route response
    for Day 10's ParseCard / CandidateList UI components.
    """

    assistant_text: str
    content_blocks: list[dict[str, Any]]
    turn_messages: list[dict[str, Any]]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


_client: Anthropic | None = None


def run_turn(
    user: AuthedUser,
    conversation_history: list[dict[str, Any]],
    user_message: str,
) -> AssistantTurn:
    """Run one full chat turn — Claude → tools → Claude → ... → final text.

    `conversation_history` is the prior messages list in Anthropic's wire
    shape: `[{"role": "user"|"assistant", "content": <blocks-or-text>}]`.
    The route handler is responsible for loading and capping it (the 5-
    turn cap from DESIGN.md §7.2.1 lives there, not here).

    Raises:
      * `UsageCapExceeded` if the user is already at/over their daily
        token cap. Checked once at entry — once a turn begins, it runs
        to completion even if mid-turn iterations push past the cap
        (overshoot bounded at one turn per DESIGN.md §11.2).
      * `ProviderRateLimited` if Anthropic returns 429 on two consecutive
        attempts (initial + one retry after 2s, per DESIGN.md §7.3).
      * `AgentLoopLimitExceeded` if the 8-iteration cap fires.
    """
    # Day 9a: entry-only cap check. Lenient mid-turn by design — finishing
    # a started turn for ~$0.02 of overshoot is better UX than aborting
    # halfway with a partial response.
    assert_within_usage_cap(user)

    client = _anthropic_client()
    model = _model_name()
    schemas = tool_schemas()
    # Day 9c: render_system_prompt returns a two-block content array.
    # Block 0 is the static preamble (cached via cache_control: ephemeral);
    # block 1 is the dynamic tail (Today is … + per-user merchants). The
    # merchant query fires once at turn entry — the set is stable across
    # the iterations below, so we don't pay it per-hop.
    system = render_system_prompt(user_jwt=user.jwt)
    prompt_hash = system_prompt_hash(system, schemas)

    messages: list[dict[str, Any]] = list(conversation_history)
    # Slice index where THIS turn's contribution starts. Everything
    # appended from here on (user-typed message + per-iteration assistant
    # blocks + tool_result blocks) is the trace persisted to
    # chat_turn_trace.messages so the next turn can replay it faithfully
    # (DESIGN.md §8.12).
    turn_start = len(messages)
    messages.append({"role": "user", "content": user_message})

    tool_calls: list[ToolCallRecord] = []
    final_blocks: list[dict[str, Any]] = []
    final_text = ""

    for _ in range(MAX_LOOP_ITERATIONS):
        create_kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=4096,
            system=system,
            tools=schemas,
            messages=messages,
        )
        try:
            response = _call_and_log(
                client=client,
                user=user,
                model=model,
                prompt_hash=prompt_hash,
                create_kwargs=create_kwargs,
            )
        except anthropic.RateLimitError:
            # Day 9a: retry once on Anthropic 429. The retry is a second
            # _call_and_log invocation so each attempt writes its own
            # ai_call_log row (Day 8 invariant: one row per
            # messages.create call). Other exceptions propagate
            # unchanged — only RateLimitError triggers the retry.
            time.sleep(2)
            try:
                response = _call_and_log(
                    client=client,
                    user=user,
                    model=model,
                    prompt_hash=prompt_hash,
                    create_kwargs=create_kwargs,
                )
            except anthropic.RateLimitError as exc:
                raise ProviderRateLimited() from exc

        # Snapshot the assistant's blocks. The full block sequence (text
        # + tool_use) is what we replay to Claude on the next iteration,
        # and it's what we persist to chat_messages.content_blocks.
        assistant_blocks = [_block_to_dict(b) for b in response.content]
        final_blocks = assistant_blocks
        final_text = "".join(
            b.get("text", "") for b in assistant_blocks if b.get("type") == "text"
        )

        # Append the assistant turn to the running message list — Anthropic
        # requires the prior assistant turn (with tool_use blocks intact)
        # to be present when we send back tool_result blocks.
        messages.append({"role": "assistant", "content": assistant_blocks})

        if response.stop_reason != "tool_use":
            # Model is done (end_turn, max_tokens, stop_sequence). Whatever
            # text it produced is the final reply.
            return AssistantTurn(
                assistant_text=final_text,
                content_blocks=final_blocks,
                turn_messages=messages[turn_start:],
                tool_calls=tool_calls,
            )

        # Execute every tool_use block in this assistant turn, then loop.
        tool_results: list[dict[str, Any]] = []
        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            tool_use_id = block.get("id", "")
            try:
                tool_input = _coerce_input(block.get("input", {}))
                result = execute_tool(name, tool_input, user)
                is_error = False
            except KeyError:
                # Unknown tool name — surface as is_error tool_result so
                # Claude can recover rather than crashing the turn.
                tool_input = block.get("input", {})
                result = {"error": "unknown_tool", "name": name}
                is_error = True
            except Exception as exc:
                # Tool implementation raised. Same recovery path — the
                # model gets to see the error and decide what to do.
                tool_input = block.get("input", {})
                result = {"error": "tool_failed", "detail": str(exc)}
                is_error = True

            tool_calls.append(
                ToolCallRecord(name=name, input=dict(tool_input), result=result)
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(result),
                **({"is_error": True} if is_error else {}),
            })

        # Send all tool_result blocks back as a single user-role message;
        # this is the wire shape Anthropic expects.
        messages.append({"role": "user", "content": tool_results})

    raise AgentLoopLimitExceeded(
        f"agent loop did not converge within {MAX_LOOP_ITERATIONS} iterations"
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _anthropic_client() -> Anthropic:
    """Lazy singleton — matches the gemini integration pattern so import
    of this module doesn't require ANTHROPIC_API_KEY at import time. Tests
    monkeypatch _client (or the module-level alias) to inject mocks."""
    global _client
    if _client is None:
        # The SDK reads ANTHROPIC_API_KEY from env automatically; surfacing
        # the missing-key case here gives a sharper error than the SDK's
        # default authentication failure on first call.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AgentLoopError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic()
    return _client

def _model_name() -> str:
    """Support model name."""
    return os.environ.get("ANTHROPIC_MODEL") or _DEFAULT_CHAT_MODEL

def _block_to_dict(block: Any) -> dict[str, Any]:
    """Anthropic SDK content blocks are pydantic models; chat_messages
    persistence and tool_result construction both want plain dicts. Use
    the SDK's serializer where possible, fall back to a manual shape so
    SDK version drift doesn't bite."""
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump()
    # Best-effort fallback for mocks/stubs in tests.
    return dict(block) if hasattr(block, "keys") else {"type": "unknown"}

def _coerce_input(raw: Any) -> dict[str, Any]:
    """tool_use.input is typed as dict in the SDK but mocks may pass a
    JSON string — accept both. A non-dict, non-string is a programmer
    error, not a runtime branch."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise TypeError(f"tool_use.input must be dict or JSON string, got {type(raw).__name__}")

def _call_and_log(
    *,
    client: Anthropic,
    user: AuthedUser,
    model: str,
    prompt_hash: str,
    create_kwargs: dict[str, Any],
) -> Any:
    """One messages.create attempt + one ai_call_log row.

    The audit row is written in a `finally` block so success and failure
    each produce exactly one row per attempt. Day 8's load-bearing
    invariant — "one ai_call_log row per messages.create call" — must
    hold even on the retry path; otherwise rate-limit incidents and
    provider latency disappear from cost / reliability analytics
    (DESIGN.md §8.8).

    This helper encapsulates what was previously inline at the top of
    each loop iteration. Pulling it out lets the retry path (a second
    call on `RateLimitError`) reuse the exact same logging shape
    without duplicating the try/except/log code, and without collapsing
    two API calls into one row the way `with_429_backoff` did.
    """
    start = time.perf_counter()
    success = False
    error_code: str | None = None
    input_tokens = 0
    output_tokens = 0
    try:
        response = client.messages.create(**create_kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        success = True
        return response
    except Exception as exc:
        error_code = type(exc).__name__
        raise
    finally:
        log_ai_call(
            user.jwt,
            user_id=user.user_id,
            provider="anthropic",
            model=model,
            task_type="chat_turn",
            prompt_version=PROMPT_VERSION,
            prompt_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
            success=success,
            error_code=error_code,
        )
