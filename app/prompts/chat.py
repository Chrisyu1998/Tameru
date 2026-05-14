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
  * chat_v3 (Day 9b) — adds `propose_transaction` (returns proposal, no
    DB write) and `set_goal` (direct-write carve-out). Rewrites the
    read-only paragraph from chat_v2 to reflect the new surface, and
    teaches the get_cards → propose_transaction handoff for cards named
    by alias.
  * chat_v4 (Day 9c) — `render_system_prompt` now returns a two-block
    content array with an Anthropic `cache_control: ephemeral` marker on
    block[0] (the static preamble) and per-user content in block[1]
    (`Today is …` + the user's top-30 merchants from `top_user_merchants`
    so Claude canonicalizes "KFC" → "Kentucky Fried Chicken" on
    `propose_transaction`). The cache breakpoint is what keeps the
    §11.3 cost projection valid — without it, per-user variation in
    the prompt would invalidate the cache for every user.

Hash policy: system_prompt_hash() hashes block[0]["text"] + tool schemas
only. The dynamic tail (block[1]) is deliberately excluded so two
different users on the same chat_v4 prompt produce the same prompt_hash
— that's what keeps `ai_call_log.prompt_hash` useful for eval and
cost-curve bucketing. The user's chat message is NOT in the hash input —
privacy posture (CLAUDE.md). A reversible hash isn't the threat; the
principle is that user-typed text doesn't flow into the audit log even
in derived form.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any

from app.db import supabase_for_user

PROMPT_VERSION = "chat_v4"


SYSTEM_PROMPT = """\
You are Tameru's spending-intelligence assistant. The user can ask you about \
their own transactions, cards, and subscriptions. Their data is scoped to \
them — every tool call you make runs with their identity, so you cannot see \
anyone else's data.

## Tools

Pick the tool that matches the question shape, not the keyword:

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
multipliers. Use for "what cards do I have", "which card earns most on X", \
or to resolve a card the user named by alias before calling \
propose_transaction.

- **propose_transaction**: builds a transaction proposal from a \
user-described purchase. Returns a payload the client renders as a parse \
card; the row is only written when the user taps "looks right" in the UI. \
**This tool does not add the transaction.** After calling it, tell the user \
something like "here's the parse — tap looks right to add it." Do NOT say \
"I've added it" or "added successfully" — the row does not exist yet. If \
the user names a card ("on my Amex Gold"), call get_cards first to look up \
the UUID, then pass it as card_id; do not call get_cards more than once per \
turn — reuse the result already in your context. If two cards match the \
name ambiguously, ask the user which one before proposing.

- **set_goal**: sets a spending budget for a (category, period) slot. \
"Set" means replace — calling set_goal twice for the same (category, \
period) overwrites the prior value rather than adding a second goal. Omit \
category to set an overall budget across all categories.

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

You can propose new transactions and set spending goals, but you cannot \
edit or delete existing transactions, cards, or subscriptions. If the user \
asks to change or remove existing data, tell them to use the transactions \
list (tap a row to edit) or the cards page.
"""


def render_system_prompt(
    user_jwt: str,
    today: _dt.date | None = None,
) -> list[dict[str, Any]]:
    """Return the chat turn's system prompt as a two-block content array.

    Request:
        user_jwt: caller's JWT — used by render_user_merchants() to read
            the `top_user_merchants` view under RLS. Required.
        today:    test seam. Production callers omit it.

    Response:
        [
            {"type": "text", "text": SYSTEM_PROMPT,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Today is YYYY-MM-DD.\\n\\n<merchants block>"},
        ]

    Block 0 is the static preamble — identical across users and across
    turns. The `cache_control: ephemeral` marker tells Anthropic to hash
    the prefix up to this point and reuse the cached attention state for
    5 minutes. Every user shares the same cached prefix, which is what
    keeps the §11.3 cost projection's 90%-cache-read discount valid.

    Block 1 is the dynamic tail — per-user, per-day. The `Today is …`
    line lets Claude resolve "today", "last week", and the date arg on
    propose_transaction; the merchants block teaches canonicalization
    ("KFC" → "Kentucky Fried Chicken" so the user's history doesn't
    fragment across spelling variants). Both items are deliberately
    outside the cache: the date changes daily (cache TTL is 5 minutes,
    so this doesn't matter much in practice), and the merchants list
    varies per user (which is exactly the invalidation we're avoiding
    by putting it after the breakpoint).

    Anthropic accepts either a string or a list-of-blocks for the
    `system` parameter on messages.create — the loop passes this list
    through unchanged.
    """
    if today is None:
        today = _dt.date.today()
    dynamic_tail = (
        f"Today is {today.isoformat()}.\n\n"
        + render_user_merchants(user_jwt)
    )
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_tail,
        },
    ]


def render_user_merchants(user_jwt: str) -> str:
    """Return the per-user merchants block for the system-prompt tail.

    Reads the `top_user_merchants` view (90-day window, top 30 by
    frequency then recency). The view has `security_invoker = true` so
    the user's JWT scopes the result — passing a different user's JWT
    returns that user's merchants, never both.

    Output shape, populated case:

        The user's top merchants from the last 90 days, ordered by frequency:
        - Kentucky Fried Chicken (12 visits, last 3 days ago)
        - Trader Joe's (8 visits, last 1 day ago)
        - ...

        When the user mentions a merchant whose spelling closely matches one
        of these (KFC ≈ Kentucky Fried Chicken, TJs ≈ Trader Joe's), use the
        exact spelling from this list when calling propose_transaction. This
        keeps the user's history from fragmenting across spelling variants.

    Output shape, empty case (new user with no transactions):

        (No prior merchants yet.)

    The empty-case string is intentionally minimal — telling Claude to
    "use the user's own spelling" is redundant with default behavior and
    just burns tokens on every cold-start turn. The block is always
    present (never an empty string) so downstream code branching on
    "is there a merchants block" stays simple.

    Call cadence: once per turn at entry, not per loop iteration. The
    merchant set is stable across the turn, and at v1 scale (~30 rows
    out of a few thousand transactions, indexed on user_id+date) the
    read is cheap.
    """
    client = supabase_for_user(user_jwt)
    rows = (
        client.table("top_user_merchants")
        .select("merchant, freq_90d, last_seen")
        .execute()
        .data
        or []
    )
    if not rows:
        return "(No prior merchants yet.)"

    today = _dt.date.today()
    lines = ["The user's top merchants from the last 90 days, ordered by frequency:"]
    for row in rows:
        last_seen = _dt.date.fromisoformat(row["last_seen"])
        ago = _humanize_days_ago((today - last_seen).days)
        lines.append(
            f"- {row['merchant']} ({row['freq_90d']} visits, last {ago})"
        )
    lines.append("")
    lines.append(
        "When the user mentions a merchant whose spelling closely matches "
        "one of these (KFC ≈ Kentucky Fried Chicken, TJs ≈ Trader Joe's), "
        "use the exact spelling from this list when calling "
        "propose_transaction. This keeps the user's history from "
        "fragmenting across spelling variants."
    )
    return "\n".join(lines)


def system_prompt_hash(
    rendered: list[dict[str, Any]] | str,
    tool_schemas: list[dict[str, Any]],
) -> str:
    """SHA-256 of the cached preamble + canonical-JSON tool schemas.

    Goes into ai_call_log.prompt_hash. Hashes block[0]["text"] (the
    static preamble) only — the dynamic tail in block[1] varies per
    user and per day, which would defeat the hash's purpose (bucketing
    eval and cost-curve queries by prompt version). Two different users
    on the same chat_v4 prompt must produce the same hash; changing
    SYSTEM_PROMPT or any tool schema must change it.

    Accepts the legacy string shape too, for forward-compat with any
    callers we missed during the chat_v4 migration. New code should
    pass the block list returned by render_system_prompt.
    """
    if isinstance(rendered, str):
        preamble = rendered
    else:
        preamble = rendered[0]["text"]
    payload = preamble + "\n---tools---\n" + json.dumps(
        tool_schemas, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _humanize_days_ago(days: int) -> str:
    """Render a day delta the way the merchants block reads naturally.

    `days=0` → "today"; `days=1` → "1 day ago"; otherwise "N days ago".
    Negative deltas (future-dated transactions) shouldn't appear in the
    top_user_merchants view but are clamped to "today" defensively.
    """
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"
