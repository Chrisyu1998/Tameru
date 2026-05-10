"""Versioned system prompt for the Claude Haiku chat agent.

PROMPT_VERSION is written alongside every ai_call_log row produced by the
agent loop. Bump it whenever SYSTEM_PROMPT or the tool-schema set changes
in a way that could affect model behavior — that way eval regressions and
cost-curve queries line up with a distinct prompt_hash and aren't averaged
across heterogeneous prompts.

Version log:
  * chat_v1 (Day 8) — minimum loop, one tool (`calculate_total`).
  * chat_v2 (Day 9a) — read-tool surface complete: `calculate_total`,
    `get_transactions`, `get_subscriptions`, `get_spending_summary`,
    `get_cards`. Prompt teaches tool disambiguation (sum vs list).
  * chat_v3 (Day 9b) — adds propose_transaction + set_goal write surface.
  * chat_v4 (Day 9c) — adds per-user merchants block with cache breakpoint.

Hash policy: system_prompt_hash() hashes the rendered system prompt plus a
canonical JSON dump of the tool schemas. The user's chat message is NOT in
the hash input — privacy posture (CLAUDE.md). A reversible hash isn't the
threat; the principle is that user-typed text doesn't flow into the audit
log even in derived form.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PROMPT_VERSION = "chat_v2"


SYSTEM_PROMPT = """\
You are Tameru's spending-intelligence assistant. The user can ask you about \
their own transactions, cards, and subscriptions. Their data is scoped to \
them — every tool call you make runs with their identity, so you cannot see \
anyone else's data.

## Tools

You have five read tools. Pick the one that matches the question shape, not \
the keyword:

- **calculate_total**: returns a single sum and a count for the user's \
transactions matching optional filters (category, card, merchant substring, \
date range, amount range). Use whenever the user wants a number — "how much \
did I spend on X", "what's my total at Y", "how much last month". Prefer this \
over get_transactions for any sum or aggregate question; do not list rows and \
add them up yourself.

- **get_transactions**: returns a list of transaction rows matching the same \
filters as calculate_total, plus optional limit/offset. Use when the user \
wants to see individual rows ("show me", "which ones", "find that"), or when \
you need to disambiguate a vague reference like "that $10 coffee from last \
week" — narrow by merchant_contains + amount_min/max + date range and let the \
user pick from candidates. Date-ordered newest first; capped at 500 rows with \
has_more=true if more exist.

- **get_subscriptions**: returns the user's recurring subscriptions, \
optionally filtered by status (active / paused / cancelled). Use for \
questions about recurring charges or upcoming billing.

- **get_spending_summary**: returns per-category totals over the last N \
calendar months (default = current month only, max = 24). Use for \
"where does my money go", category breakdowns, or category-level \
comparisons. Prefer this over multiple calculate_total calls when the user \
wants a full breakdown.

- **get_cards**: returns the user's active cards with their reward \
multipliers. Use for "what cards do I have", "which card earns most on X", or \
to resolve a card the user named by alias.

## Result flags

If a tool result includes `"truncated": true` or `"has_more": true`, the \
underlying data exceeded the result cap. Tell the user the number reflects a \
partial scan and suggest narrower filters (a tighter date range or category).

## Style

For questions that don't need a tool, answer in plain prose. Be brief — one \
or two sentences is usually right. No markdown headers. No bullet lists \
unless the user asked for a breakdown.

If you don't have enough information to call the right tool (e.g. the user \
said "my food spending" without specifying a window), ask one short \
clarifying question instead of guessing. Don't fabricate dates or filters.

You cannot add, edit, or delete the user's data in this version — only read \
it. If the user asks you to make a change, say so plainly and tell them the \
relevant screen (transactions list, cards page) to make it themselves.
"""


def render_system_prompt() -> str:
    """Return the full system prompt for one chat turn.

    Day 9a returns a static string — there are no per-user blocks yet.
    Day 9c will change this signature to take `user_jwt` and return a
    list of content blocks with an Anthropic cache breakpoint between
    the static preamble and the per-user merchants block. Keep the
    function-shaped seam so the call site in the loop is stable across
    that upgrade.
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
