"""Cross-session memory — Day 16 (DESIGN.md §7.6 layer 2).

Two responsibilities live in this module:

  * `distill_session(user_jwt, conversation_id)` — reads the full
    chat_messages history for one conversation, asks Claude Haiku to
    extract atomic facts, upserts each into `user_memory` via the
    `upsert_user_memory_fact` RPC, and marks the conversation done by
    inserting a `conversation_distillation_state` row. Wrapped in a
    blanket try/except so a distillation failure does not 500 the chat
    turn that scheduled it.

  * `render_user_memory(user_jwt)` — reads up to 60 facts ordered by
    `relevance_score DESC, reinforced_at DESC` and formats them as a
    bulleted block for injection into the chat system prompt's dynamic
    tail (block[1]). Returns the empty string on any failure so the
    chat path always has a usable value to concatenate.

Both functions run under the caller's JWT (CLAUDE.md invariant 14). No
service role anywhere.

Anthropic client indirection mirrors `app/agent/loop.py`: a lazy
module-level singleton is created on first call, swappable via
`monkeypatch.setattr(memory, "_anthropic_client", ...)` in tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any
from uuid import UUID

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.db import supabase_for_user
from app.integrations.aicalllog import AICallLogError, log_ai_call

logger = logging.getLogger(__name__)


# Facts at or above this score are treated as "high-confidence" in the
# capacity-cap pass (Day 17); this module just stores what Haiku returns
# and lets the cap sort them out later. Defined here so test scaffolds can
# reference it without re-deriving the §7.6 threshold.
MIN_CONVERSATION_MESSAGES = 4
MAX_FACTS_RENDERED = 60
ALLOWED_CATEGORIES = frozenset(
    {"spending_pattern", "preference", "active_context", "card_preference", "goal"}
)

# ai_call_log.prompt_version for the distillation system prompt. Bump
# when DISTILL_SYSTEM_PROMPT changes in a way that could affect what
# Haiku extracts — eval / cost-curve bucketing relies on it.
PROMPT_VERSION = "memory_distill_v2"

_DEFAULT_MODEL = "claude-haiku-4-5"

DISTILL_SYSTEM_PROMPT = """\
You are a memory-distillation pass for a personal-finance assistant. \
Read the conversation and extract atomic facts about the user that \
should persist across future sessions.

Each fact must be:
  * About the user (their goals, habits, preferences, active context, \
or card setups). Do not extract facts about the assistant, the data, \
or generic financial knowledge.
  * Self-contained — a future turn must be able to use the fact \
without seeing this conversation.
  * One claim per fact. Compound facts ("user likes X and dislikes Y") \
must be split.

Do NOT extract live-ledger state. Specifically, do not extract:
  * Which cards the user currently owns (e.g. "User has Amex Platinum \
1007"). Cards can be deleted via the cards page; the live database \
(via the get_cards tool) is the source of truth.
  * Which subscriptions are active, or any specific transaction. These \
are queried live via get_subscriptions / get_transactions and can \
change outside chat.
Card and subscription HABITS are fine ("User puts Costco runs on CSR"). \
Ownership/inventory is not.

Category vocabulary (use exactly these strings):
  * spending_pattern  — recurring habits, e.g. "User eats out 3x/week \
on average".
  * preference        — non-card preferences, e.g. "User prefers groceries \
over dining for rewards".
  * active_context    — short-lived facts, e.g. "User is planning a Tokyo \
trip in spring 2027". These naturally decay (Day 17).
  * card_preference   — card-specific habits, e.g. "User puts Costco runs \
on CSR".
  * goal              — explicit objective with a target/timeline, e.g. \
"User is working toward CSR $4K SUB by Q2 2026".

Score each fact 0.0–1.0 by enduring relevance: 1.0 = will matter a year \
from now; 0.3 = passing comment, may not matter next week.

Return a JSON array of objects with keys `fact`, `category`, \
`relevance_score`. Return only the JSON array — no prose, no markdown \
fences. If there is nothing worth remembering, return `[]`.
"""


class _DistilledFact(BaseModel):
    """One fact returned by the distillation model.

    Validates the category against the schema CHECK constraint so a
    hallucinated category short-circuits before reaching the DB (where
    the CHECK would reject it anyway, but with a less-actionable error).
    """

    fact: str = Field(min_length=1)
    category: str
    relevance_score: float = Field(ge=0.0, le=1.0)

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str) -> str:
        """Reject categories outside the schema CHECK enum."""
        if value not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"invalid category {value!r}; allowed: {sorted(ALLOWED_CATEGORIES)}"
            )
        return value


_client: Anthropic | None = None


def distill_session(user_jwt: str, conversation_id: UUID) -> None:
    """Distill one conversation's facts into `user_memory`.

    Request:
        user_jwt:        caller's JWT — drives RLS on every DB write.
        conversation_id: which conversation to distill.

    Response: None. Side effects on success:
        * 0..N `user_memory` rows upserted via the RPC.
        * One `conversation_distillation_state` row inserted, marking the
          conversation done so the piggyback predicate skips it forever.
        * One `ai_call_log` row with `task_type='memory_distill'`.

    Fast-paths (no Anthropic call, no DB writes other than what's noted):
        * Conversation already has a `conversation_distillation_state`
          row — return immediately.
        * `chat_messages` row count < MIN_CONVERSATION_MESSAGES — return
          without writing a state row, so a longer follow-up in the same
          conversation can trigger distillation later.

    Failure posture: any exception below the fast-path checks is
    caught, logged, and swallowed. The `conversation_distillation_state`
    row is NOT inserted on failure, so the next piggyback firing
    retries the conversation.
    """
    try:
        client = supabase_for_user(user_jwt)

        if _already_distilled(client, conversation_id):
            return

        rows = (
            client.table("chat_messages")
            .select("role, content_blocks")
            .eq("conversation_id", str(conversation_id))
            .order("seq")
            .execute()
            .data
            or []
        )
        if len(rows) < MIN_CONVERSATION_MESSAGES:
            return

        user_id = _resolve_user_id(client, conversation_id)
        if user_id is None:
            # Defensive: should never happen if the conversation has rows
            # under RLS, but if it does we don't want to write a state row.
            return

        transcript = _format_transcript(rows)
        facts, usage, latency_ms, success, error_code = _call_haiku(transcript)

        prompt_hash_value = hashlib.sha256(
            DISTILL_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest()
        try:
            log_ai_call(
                user_jwt,
                user_id=user_id,
                provider="anthropic",
                model=_model_name(),
                task_type="memory_distill",
                prompt_version=PROMPT_VERSION,
                prompt_hash=prompt_hash_value,
                input_tokens=usage["input"],
                output_tokens=usage["output"],
                latency_ms=latency_ms,
                success=success,
                error_code=error_code,
            )
        except AICallLogError:
            logger.exception("ai_call_log write failed for memory_distill")

        if not success:
            return

        for fact in facts:
            try:
                client.rpc(
                    "upsert_user_memory_fact",
                    {
                        "p_fact": fact.fact,
                        "p_category": fact.category,
                        "p_relevance_score": fact.relevance_score,
                    },
                ).execute()
            except Exception:
                # One bad fact must not prevent others from landing. The
                # state row still gets written below — partial success is
                # acceptable for a best-effort enrichment pass.
                logger.exception(
                    "upsert_user_memory_fact failed for conversation %s",
                    conversation_id,
                )

        client.table("conversation_distillation_state").insert(
            {
                "conversation_id": str(conversation_id),
                "user_id": str(user_id),
            }
        ).execute()
    except Exception:
        # Distillation is enrichment — never propagate. The piggyback
        # predicate retries on the next chat turn because we didn't write
        # the state row.
        logger.exception(
            "distill_session failed for conversation %s", conversation_id
        )


def render_user_memory(user_jwt: str) -> str:
    """Return the user's distilled memory as a bulleted block, or "".

    Output shape (populated case):

        What I know about this user:
        - [goal] User is working toward CSR $4K SUB by Q2 2026
        - [card_preference] User puts Costco purchases on CSR
        ...

    Empty case returns "" (no header) so the dynamic-tail builder can
    concatenate unconditionally without producing a dangling label.

    Failure posture: any DB error is logged and "" is returned. A chat
    turn must never 500 because memory is unreachable — parity with
    `render_user_merchants` in `app/prompts/chat.py`.
    """
    try:
        client = supabase_for_user(user_jwt)
        rows = (
            client.table("user_memory")
            .select("fact, category")
            .order("relevance_score", desc=True)
            .order("reinforced_at", desc=True)
            .limit(MAX_FACTS_RENDERED)
            .execute()
            .data
            or []
        )
    except Exception:
        logger.exception("render_user_memory read failed; degrading to empty")
        return ""

    if not rows:
        return ""

    lines = ["What I know about this user:"]
    for row in rows:
        lines.append(f"- [{row['category']}] {row['fact']}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _anthropic_client() -> Anthropic:
    """Lazy singleton — matches loop.py's pattern. Tests monkeypatch
    this attribute directly to inject a recording or scripted client."""
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic()
    return _client


def _model_name() -> str:
    """Distillation model — env override matches the rest of the agent."""
    return os.environ.get("ANTHROPIC_MEMORY_MODEL") or _DEFAULT_MODEL


def _already_distilled(client: Any, conversation_id: UUID) -> bool:
    """Return True if a `conversation_distillation_state` row exists.

    Used as a fast-path inside `distill_session` so a duplicate piggyback
    schedule (e.g. two near-simultaneous chat turns both observing the
    same idle conversation) does not result in two Haiku calls or two
    rounds of upserts."""
    existing = (
        client.table("conversation_distillation_state")
        .select("conversation_id")
        .eq("conversation_id", str(conversation_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(existing)


def _resolve_user_id(client: Any, conversation_id: UUID) -> UUID | None:
    """Pull the user_id off any chat_messages row in the conversation.

    Used for the `ai_call_log` foreign key and the
    `conversation_distillation_state.user_id`. Under RLS the caller can
    only see their own rows, so the value is intrinsically correct."""
    row = (
        client.table("chat_messages")
        .select("user_id")
        .eq("conversation_id", str(conversation_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not row:
        return None
    return UUID(row[0]["user_id"])


def _format_transcript(rows: list[dict[str, Any]]) -> str:
    """Render chat_messages rows into a `role: text` transcript for Haiku.

    `content_blocks` is JSONB; for human-visible chat_messages every block
    is a text block (synthetic tool_use lives in `chat_turn_trace`, not
    here), so a simple text-only join is correct."""
    out: list[str] = []
    for row in rows:
        role = row.get("role", "?")
        blocks = row.get("content_blocks") or []
        text = " ".join(
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if text:
            out.append(f"{role}: {text}")
    return "\n".join(out)


def _call_haiku(
    transcript: str,
) -> tuple[list[_DistilledFact], dict[str, int], int, bool, str | None]:
    """Invoke Haiku once and parse the returned JSON.

    Returns: (facts, usage_dict, latency_ms, success, error_code).

    On parse or validation failure, returns `(facts=[], success=False,
    error_code=<exception class name>)` so the caller can still log the
    ai_call_log row with accurate token counts but skip the upsert pass.
    """
    client = _anthropic_client()
    start = time.perf_counter()
    usage = {"input": 0, "output": 0}
    try:
        response = client.messages.create(
            model=_model_name(),
            max_tokens=2048,
            system=DISTILL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return ([], usage, latency_ms, False, type(exc).__name__)

    latency_ms = int((time.perf_counter() - start) * 1000)
    usage_obj = getattr(response, "usage", None)
    if usage_obj is not None:
        usage["input"] = int(getattr(usage_obj, "input_tokens", 0) or 0)
        usage["output"] = int(getattr(usage_obj, "output_tokens", 0) or 0)

    text_blocks = [
        b for b in getattr(response, "content", []) if _block_type(b) == "text"
    ]
    raw_text = "".join(_block_text(b) for b in text_blocks).strip()
    if not raw_text:
        return ([], usage, latency_ms, True, None)

    try:
        parsed = json.loads(_strip_code_fence(raw_text))
    except json.JSONDecodeError as exc:
        logger.warning("memory_distill: model returned non-JSON: %s", exc)
        return ([], usage, latency_ms, False, "JSONDecodeError")

    if not isinstance(parsed, list):
        return ([], usage, latency_ms, False, "NotAList")

    facts: list[_DistilledFact] = []
    for item in parsed:
        try:
            facts.append(_DistilledFact.model_validate(item))
        except ValidationError as exc:
            # errors(include_input=False) — the default str(exc) embeds
            # `input_value=`, which here is distilled chat-derived fact
            # text (user content; audit P3-21). loc/msg/type carry the
            # diagnostic value without the content.
            logger.warning(
                "memory_distill: skipped invalid fact: %s",
                exc.errors(include_input=False, include_url=False),
            )
    return (facts, usage, latency_ms, True, None)


def _block_type(block: Any) -> str | None:
    """Extract `.type` from an SDK block or plain dict."""
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_text(block: Any) -> str:
    """Extract `.text` from an SDK block or plain dict."""
    if isinstance(block, dict):
        return block.get("text", "") or ""
    return getattr(block, "text", "") or ""


def _strip_code_fence(text: str) -> str:
    """Tolerate model output wrapped in ```json fences.

    The system prompt forbids markdown fences, but Haiku occasionally
    emits them anyway. Strip a leading fence + optional language tag and
    a trailing fence — anything in between is what we parse.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence line.
    first_nl = stripped.find("\n")
    if first_nl == -1:
        return stripped
    body = stripped[first_nl + 1 :]
    if body.endswith("```"):
        body = body[: -3]
    return body.strip()
