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

# Re-distillation cadence (T3, 2026-07-03). Once a conversation has been
# distilled through message N, the current-turn probe
# (find_conversation_to_distill) only re-schedules it after it grows by at
# least this many more committed messages. Keeps a long, actively-growing
# conversation captured without a Haiku call on every single turn. Passed
# to the RPC from the chat route AND used as distill_session's own
# race-guard, so the two must agree.
REDISTILL_DELTA = 4

ALLOWED_CATEGORIES = frozenset(
    {"spending_pattern", "preference", "active_context", "card_preference", "goal"}
)

# ai_call_log.prompt_version for the distillation system prompt. Bump
# when DISTILL_SYSTEM_PROMPT changes in a way that could affect what
# Haiku extracts — eval / cost-curve bucketing relies on it. v3 (2026-07-03)
# rewrote the prompt to be less conservative and to invite spending
# *patterns* out of ledger-heavy chat (few-shot examples, E1/E2).
PROMPT_VERSION = "memory_distill_v3"

_DEFAULT_MODEL = "claude-haiku-4-5"

DISTILL_SYSTEM_PROMPT = """\
You are a memory-distillation pass for a personal-finance assistant. \
Read the conversation and extract durable facts about the USER that will \
make future sessions feel personal and informed — their habits, \
preferences, goals, plans, and how they like to use their cards.

Be generous. Most real conversations contain at least one thing worth \
remembering. Err toward capturing a fact when in doubt — a low-value fact \
just gets a low relevance_score and decays on its own. Only return an \
empty array when the conversation is purely mechanical (e.g. a lone "add \
$5 coffee" with no preference, plan, or habit expressed).

Each fact must be:
  * About the user — their goals, habits, preferences, active context, \
or card strategies. Not about the assistant, not generic financial \
knowledge.
  * Self-contained — a future turn must be able to use the fact without \
seeing this conversation.
  * One claim per fact. Split compound facts ("likes X and dislikes Y").

Extract enduring PATTERNS and INTENT, not raw ledger rows. A spending \
conversation is full of signal even when the individual transactions are \
noise:
  * "logged a $47 Trader Joe's run" — the transaction is noise, but "User \
grocery-shops at Trader Joe's" is a fact (spending_pattern).
  * "booked an $800 flight to Tokyo for April" — the charge is noise, but \
"User is planning a Tokyo trip in April 2027" is a fact (active_context).
  * "I always put dining on my Amex" — "User puts dining on their Amex" is \
a fact (card_preference).

Do NOT extract live inventory or one-off ledger specifics — the live tools \
(get_cards, get_subscriptions, get_transactions) are the source of truth \
for these and they change outside chat:
  * Which cards the user currently owns (e.g. "User has Amex Platinum \
1007"). A card HABIT is fine; card OWNERSHIP is not.
  * Which subscriptions are active, or the amount/date/merchant of a \
specific transaction.

Category vocabulary (use exactly these strings):
  * spending_pattern  — recurring habits, e.g. "User eats out ~3x/week".
  * preference        — non-card preferences, e.g. "User prefers earning \
on groceries over dining".
  * active_context    — time-bound facts that naturally decay, e.g. "User \
is saving for a wedding in fall 2027".
  * card_preference   — card strategies/habits, e.g. "User puts Costco \
runs on CSR".
  * goal              — explicit objective with a target/timeline, e.g. \
"User is working toward the CSR $4K SUB by Q2 2026".

Score each fact 0.0–1.0 by enduring relevance: 1.0 = still true and useful \
a year out; 0.3 = a passing comment that may not matter next week.

Return ONLY a JSON array of objects with keys `fact`, `category`, \
`relevance_score` — no prose, no markdown fences.

Example — a conversation with signal:
  user: I put my Costco runs on CSR for the points, trying to hit the $4K \
SUB by Q2
  assistant: Nice — you're $1.9K away.
  user: cool. also logging a $60 dinner at Nobu
  assistant: Added $60 dining.
Output:
  [{"fact": "User puts Costco purchases on CSR", "category": \
"card_preference", "relevance_score": 0.7}, {"fact": "User is working \
toward the CSR $4K SUB by Q2 2026", "category": "goal", "relevance_score": \
0.9}]
(The $60 Nobu dinner is a one-off transaction — not extracted.)

Example — purely mechanical, nothing to remember:
  user: add $5 coffee
  assistant: Added $5 coffee. Anything else?
  user: no thanks
Output:
  []
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
        * One `conversation_distillation_state` row upserted (monotonically —
          via `upsert_conversation_distillation_state`), recording how many
          messages this conversation was distilled through (`message_count`)
          so the probes re-distill it only after it grows by REDISTILL_DELTA.
        * One `ai_call_log` row with `task_type='memory_distill'`.

    Fast-paths (no Anthropic call, no DB writes other than what's noted):
        * `chat_messages` row count < MIN_CONVERSATION_MESSAGES — return
          without writing a state row, so a longer follow-up in the same
          conversation can trigger distillation later.
        * Already distilled and the conversation has NOT grown by at least
          REDISTILL_DELTA messages since — return. This makes a re-run on an
          unchanged conversation a clean no-op.

    Concurrency: the delta guard is read-then-act (not atomic), so two
    genuinely simultaneous schedules of the same conversation can each still
    run one Haiku call — accepted at v1 scale, and harmless (fact upserts are
    dedup-idempotent). What the guard does NOT tolerate is a regressed
    `message_count`; the monotonic GREATEST upsert prevents that, so a
    straggler task cannot lower the recorded count and over-trigger later
    re-distills.

    Failure posture: any exception below the fast-path checks is caught,
    logged, and swallowed. The `conversation_distillation_state` row is NOT
    written on failure, so the next probe retries the conversation.
    """
    try:
        client = supabase_for_user(user_jwt)

        prior_count = _distilled_message_count(client, conversation_id)

        rows = (
            client.table("chat_messages")
            .select("role, content_blocks")
            .eq("conversation_id", str(conversation_id))
            .order("seq")
            .execute()
            .data
            or []
        )
        message_count = len(rows)
        if message_count < MIN_CONVERSATION_MESSAGES:
            return
        if prior_count is not None and (message_count - prior_count) < REDISTILL_DELTA:
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

        # Record the count we distilled through (was insert-once) so the probes
        # re-schedule only after REDISTILL_DELTA more messages. Via the
        # `upsert_conversation_distillation_state` RPC rather than PostgREST
        # `.upsert()` because the write must be MONOTONIC: two overlapping
        # tasks can snapshot different live counts, and a plain last-writer-wins
        # upsert would let a straggler regress `message_count`. The RPC's
        # GREATEST(...) on conflict makes the stored count never go backward.
        client.rpc(
            "upsert_conversation_distillation_state",
            {
                "p_conversation_id": str(conversation_id),
                "p_message_count": message_count,
            },
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


def _distilled_message_count(client: Any, conversation_id: UUID) -> int | None:
    """Return the message count this conversation was last distilled through.

    `None` means the conversation has never been distilled (no state row).
    An integer is the `message_count` recorded at the last distillation —
    `distill_session` re-runs only once the live count exceeds it by
    REDISTILL_DELTA. This replaces the old boolean `_already_distilled`
    fast-path: distillation is no longer once-per-conversation, so the
    guard is "has it grown enough?" rather than "does a row exist?"."""
    existing = (
        client.table("conversation_distillation_state")
        .select("message_count")
        .eq("conversation_id", str(conversation_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not existing:
        return None
    return int(existing[0].get("message_count") or 0)


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
