"""Versioned system prompt for the Claude Haiku chat agent (Day 8).

PROMPT_VERSION is written alongside every ai_call_log row produced by the
agent loop. Bump it whenever SYSTEM_PROMPT or the tool-schema set changes
in a way that could affect model behavior, so eval regressions line up
with a distinct prompt_hash.

v1 (chat_v1): Day 8 minimum. One tool (calculate_total). Day 9 will
expand the prompt as the rest of the tool surface lands; Day 16 will
add a user-memory block. Keeping the Day 8 stub deliberately short
avoids rewriting prose that's about to change anyway.

Hash policy: system_prompt_hash() hashes the rendered system prompt
plus a canonical JSON dump of the tool schemas. The user's chat message
is **not** in the hash input — privacy posture (CLAUDE.md). A reversible
hash isn't the threat; the principle is that user-typed text doesn't
flow into the audit log even in derived form.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PROMPT_VERSION = "chat_v1"


SYSTEM_PROMPT = """\
You are Tameru's spending-intelligence assistant. The user can ask you about \
their own transactions, cards, and subscriptions. Their data is scoped to \
them — every tool call you make runs with their identity, so you cannot see \
anyone else's data.

Available tools today:
- calculate_total: sum the user's transactions matching optional filters \
(category, card_id, date_from, date_to). Use it whenever the user asks \
"how much did I spend" or any equivalent total/aggregate question. Prefer \
it over guessing.

If a tool result includes "truncated": true, the underlying data exceeded \
the result cap. Tell the user the number reflects a partial scan and \
suggest narrower filters.

For questions that don't need a tool, answer in plain prose. Be brief — \
one or two sentences is usually right. No markdown, no headers.

If you don't have enough information to call the right tool (e.g. the \
user said "my food spending" without specifying a time window), ask one \
short clarifying question instead of guessing.
"""


def render_system_prompt() -> str:
    """Return the full system prompt for one chat turn.

    Day 8 has no per-user blocks — this just returns SYSTEM_PROMPT. The
    function exists today so Day 9 (merchant block via render_user_merchants)
    and Day 16 (cross-session memory block) can extend it without changing
    the call site in the loop. When those days land, the call signature
    will grow a `user_jwt` parameter.
    """
    return SYSTEM_PROMPT


def system_prompt_hash(rendered: str, tool_schemas: list[dict[str, Any]]) -> str:
    """SHA-256 of the rendered system prompt + canonical-JSON tool schemas.

    Goes into ai_call_log.prompt_hash. Two prompts with the same rendered
    text but different tool sets produce different hashes — the model
    behaves differently when the tool surface changes, so eval comparison
    has to bucket by both. `sort_keys=True` keeps the JSON canonical
    across Python dict iteration order changes.
    """
    payload = rendered + "\n---tools---\n" + json.dumps(
        tool_schemas, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()
