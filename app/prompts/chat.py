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
  * chat_v5 (Day 14) — adds `propose_card` (returns a CardProposal,
    no DB write) and teaches Claude to ask for the card's network and
    last 4 digits before calling it. Web_search now runs inside the
    card-lookup tool impl (not the chat turn), so the surface area
    Claude reasons about is unchanged. Bumping the version busts the
    prompt cache once; the next turn re-warms it.
  * chat_v6 (Day 14 follow-up) — `propose_card` no longer asks the user
    for network or issuer (the lookup derives both from the card name)
    and stops blocking the proposal on `last_four` (the parse-card UI
    collects it before commit). Only the card name is required to fire
    the tool. Eliminates the "is that Visa or Mastercard?" friction
    that most users couldn't answer.
  * chat_v7 (Day 16) — `render_system_prompt`'s dynamic tail (block[1])
    now appends `render_user_memory(user_jwt)` after the merchants
    block. block[0] is unchanged so the §11.3 prompt-cache discount
    survives — the cache shifts breakpoint stays at SYSTEM_PROMPT's
    end. Bumping the version busts the prompt cache once; the next turn
    re-warms it. The memory text inside block[1] varies per user and is
    deliberately outside the cache breakpoint.
  * chat_v8 (Day 19) — adds `propose_subscription` (returns a
    SubscriptionProposal, no DB write) and extends `propose_card` with
    an optional `next_annual_fee_date` arg for the §6.5 AF dual-write.
    The SYSTEM_PROMPT gains a `propose_subscription` paragraph teaching
    the cardless-ACH case (omit card_id for rent / utilities / mortgage)
    and the forward-only auto-log rule (today's charge is not
    backfilled — log it manually if you want it captured). The card
    paragraph picks up a one-liner about the AF renewal-date arg.
  * chat_v9 (Day 22) — `propose_transaction` now defaults a missing
    purchase date to today instead of asking "when was this?". The
    blanket "don't fabricate dates" guidance is scoped to retrieval
    windows; transaction entry defaults the date because the parse
    card is an editable correction surface (§6.2, §7.7) and a
    clarifying-question round-trip on every dateless entry is poor UX.
    A missing *amount* is still a hole — the agent asks for that.
    Surfaced by the Day 22 chat-extraction eval (DESIGN.md §7.10).
    Bumping the version busts the prompt cache once.
  * chat_v10 (Day 22) — two tool-surface changes surfaced by the §7.10
    eval. (1) The propose_* tools now take a short card `ref` handle
    (`{issuer}-{last_four}`, e.g. "amex-1001") instead of the card's
    UUID — the eval caught Claude dropping a hex digit copying a 36-char
    UUID between get_cards and propose_subscription, silently losing the
    card attribution. The prompt teaches "copy the short ref, never the
    long id". (2) `get_spending_summary` gained explicit date_from/
    date_to; the prompt teaches that a specific named month requires an
    explicit range — relying on the trailing `months` window silently
    answered about the current month. Bumping the version busts the
    prompt cache once.
  * chat_v11 (Day 29) — internationalization (DESIGN.md §6.6). The Style
    section now instructs the agent to reply in the language the user
    wrote in (English / Japanese / Traditional Chinese — matching the
    voice-input set, §7.7) while keeping tool arguments and category
    values in canonical English, and to echo home-currency amounts
    verbatim without FX conversion. Haiku is natively multilingual, so
    no model change. Static-block edit, so it busts the prompt cache once.
  * chat_v12 (Day 29 Tier 2) — reply language is now SETTING-driven, not
    input-driven (DESIGN.md §6.6 Tier 2). The Style section tells the
    agent to reply in the user's chosen interface language (from
    `users_meta.ui_language`), echoed into the dynamic tail (block[1]) by
    `render_user_language`, falling back to mirror-the-input only when no
    language is set. Predictable beats reactive: short/neutral inputs
    ("Netflix $15", "ok") made v11 guess the language each turn. The
    per-user language lives in block[1] (NOT hashed) so every user still
    shares one cached block[0] prefix. Static-block edit busts the cache
    once.

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

from app.agent.memory import render_user_memory
from app.db import supabase_for_user

PROMPT_VERSION = "chat_v12"


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
add them up yourself. For a SPECIFIC named month or period ("in March", \
"in Q1"), compute the explicit date_from/date_to from today's date (in this \
prompt) and pass them — do not leave the window open.

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

- **get_spending_summary**: returns per-category totals over a window. \
Pass `months` for a trailing window ending today (default = current month \
only, max = 24); OR pass `date_from`/`date_to` for an explicit range. For a \
SPECIFIC named month or past period ("breakdown for March"), you MUST pass \
date_from/date_to — the trailing `months` window cannot isolate a single \
past month, and relying on the default would silently answer about the \
current month instead. Use for "where does my money go", category \
breakdowns, or category-level comparisons; prefer this over multiple \
calculate_total calls when the user wants a full breakdown.

- **get_cards**: returns the user's active cards, each with reward \
multipliers and a short `ref` handle (e.g. "amex-1001"). Use for "what \
cards do I have", "which card earns most on X", or to resolve a card the \
user named before a propose_* call — then pass that card's `ref` (never \
its long `id`) as the `card_ref` argument.

- **propose_transaction**: builds a transaction proposal from a \
user-described purchase. Returns a payload the client renders as a parse \
card; the row is only written when the user taps "looks right" in the UI. \
**This tool does not add the transaction.** After calling it, tell the user \
something like "here's the parse — tap looks right to add it." Do NOT say \
"I've added it" or "added successfully" — the row does not exist yet. \
**If the user doesn't say when the purchase happened, default the date to \
today — do NOT ask "when was this?".** The parse card lets the user correct \
the date before confirming, so a missing date is never a reason to withhold \
the proposal or to ask a follow-up. A missing *amount* is different: if the \
user gives no amount, ask for it — a proposal needs a real number. If \
the user names a card ("on my Amex Gold"), call get_cards first, then pass \
the matching card's short `ref` (e.g. "amex-1001") as `card_ref` — copy \
the short ref, never the long `id`. Do not call get_cards more than once \
per turn — reuse the result already in your context. If two cards match \
the name ambiguously, ask the user which one before proposing.

- **propose_card**: builds a card proposal when the user wants to add a \
credit card to their wallet ("add my Chase Sapphire Reserve", "I got an \
Amex Gold"). The tool runs an authoritative-source web lookup to fill in \
the rewards program, issuer, network, category multipliers, annual fee, \
and citations from the card name alone. Returns a payload the client \
renders as a parse card; the row is only written when the user taps \
"looks right." **This tool does not add the card.** Do NOT say "I've \
added it" — the row does not exist yet. \
Only the card name (the `program` argument) is required. **Do NOT ask \
the user which network or issuer their card is on** — the lookup fills \
both from the card name (Chase Sapphire = Visa + Chase, Amex Gold = \
Amex + Amex, Citi Double Cash = Mastercard + Citi). Pass `network` only \
if the user explicitly named it ("my Visa Sapphire"). Pass `last_four` \
if the user said it ("ending 4321"); otherwise omit and the parse-card \
UI surfaces an input the user fills before tapping confirm. The user \
can have two cards of the same product (two Amex Platinums on the same \
account), so the last 4 is what disambiguates them — but don't block \
the proposal flow to collect it. If the user mentions when the annual \
fee hits ("renews in March", "AF is March 15"), pass `next_annual_fee_date`; \
otherwise omit — do not guess, the date is per-user and the web doesn't \
know it. \
**For any add-card intent, always call propose_card.** Do not refuse \
based on chat history or memory — cards can be removed from the wallet \
via the cards page, which leaves no chat record, so prior add-turns in \
this conversation are not evidence the card is still active. If you \
need to verify before proposing (e.g. to disambiguate which existing \
card the user meant), call get_cards first; otherwise just propose.

- **propose_subscription**: builds a subscription proposal for a \
recurring charge the user wants to TRACK ("track my Netflix at \
$15.99/month", "my rent is $2400 monthly", "add my Spotify family"). \
Returns a payload the client renders as a parse card; the row is only \
written when the user taps "looks right." **This tool does not add the \
subscription.** Do NOT say "I've added it" — the row does not exist yet. \
If the user names a card ("on my Amex Gold"), call get_cards first and \
pass the matching card's short `ref` (e.g. "amex-1001") as `card_ref` — \
copy the short ref, never the long `id`. If the user doesn't mention \
a card (rent, utilities, mortgage, anything paid by bank ACH), OMIT \
`card_ref` — cardless subscriptions are first-class and the auto-logger \
records them with no card attribution. \
The first auto-logged transaction fires on the NEXT billing cycle — \
today's charge is NOT backfilled. Tell the user something like "I'll \
track this going forward — log today's charge manually if you want it \
in the ledger." If the user mentions paying a recurring bill today \
and also wants to record today's charge, call propose_transaction \
separately for today and propose_subscription for the recurring track. \
Don't combine the two — they're separate proposals.

- **set_goal**: sets a spending budget for a (category, period) slot. \
"Set" means replace — calling set_goal twice for the same (category, \
period) overwrites the prior value rather than adding a second goal. Omit \
category to set an overall budget across all categories.

## Result flags

If a tool result includes `"truncated": true` or `"has_more": true`, the \
underlying data exceeded the result cap. Tell the user the number reflects a \
partial scan and suggest narrower filters (a tighter date range or category).

## Style

Reply in the user's chosen interface language, given in the context block \
below as "The user's interface language is …". Always answer in that language \
regardless of which language the user typed in — if their interface language \
is Japanese, reply in Japanese even when they wrote to you in English. If no \
interface language is given, fall back to replying in the same language the \
user wrote in. This applies only to your prose **to the user** — tool \
arguments, category values (e.g. "Dining", "Groceries"), and card refs stay \
in their canonical English form regardless of the reply language. Amounts you \
mention in prose carry the user's home-currency symbol exactly as the tools \
return them; do not convert currencies.

For questions that don't need a tool, answer in plain prose. Be brief — one \
or two sentences is usually right. No markdown headers. No bullet lists \
unless the user asked for a breakdown.

If you don't have enough information to call the right tool (e.g. the user \
said "my food spending" without specifying a window), ask one short \
clarifying question instead of guessing. Don't invent retrieval windows or \
filter values. Entering a transaction is the exception — a missing purchase \
date defaults to today (see propose_transaction); only a missing amount \
warrants a follow-up.

You can propose new transactions and set spending goals, but you cannot \
edit or delete existing transactions, cards, or subscriptions. If the user \
asks to change or remove existing data, tell them to use the transactions \
list (tap a row to edit) or the cards page.

Do not claim a card, transaction, or subscription is already in the user's \
wallet without verifying with a tool call (get_cards, get_transactions, \
get_subscriptions). Chat history and memory are not authoritative for \
ledger state — the user can change it outside chat at any time.
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
    fragment across spelling variants); the memory block (Day 16) lists
    the user's distilled cross-session facts so Claude grounds answers
    in what it already knows about them. All three items are deliberately
    outside the cache: the date changes daily, the merchants list varies
    per user, and the memory varies per user (each is exactly the
    invalidation we're avoiding by putting it after the breakpoint).

    Anthropic accepts either a string or a list-of-blocks for the
    `system` parameter on messages.create — the loop passes this list
    through unchanged.
    """
    if today is None:
        today = _dt.date.today()
    # Day 16: memory block lands AFTER the merchants block, both inside
    # block[1]. Keeping it in the dynamic tail is load-bearing — per-
    # user memory inside the cached preamble (block[0]) would invalidate
    # the prefix cache for every user and break the §11.3 cost
    # projection. `render_user_memory` returns "" on empty / error in
    # the normal path; the outer try/except below is a defense-in-depth
    # safety net so a future refactor that drops the inner guard cannot
    # 500 the chat turn.
    try:
        memory_block = render_user_memory(user_jwt)
    except Exception:  # noqa: BLE001
        memory_block = ""
    dynamic_tail = (
        f"Today is {today.isoformat()}.\n\n"
        + render_user_merchants(user_jwt)
    )
    if memory_block:
        dynamic_tail = dynamic_tail + "\n\n" + memory_block
    # chat_v12 (Day 29 Tier 2): the per-user reply language lives in the
    # dynamic tail, never block[0] — keeping it out of the hashed preamble
    # so every user shares one cached prefix (§11.3). Empty string when the
    # user has no explicit language → block[0]'s mirror-the-input fallback.
    language_block = render_user_language(user_jwt)
    if language_block:
        dynamic_tail = dynamic_tail + "\n\n" + language_block
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


# chat_v12 reply-language directive. Maps the stored ui_language code to a
# human language name for the prompt. Mirrors app/util/language.py's
# supported set; an unknown/NULL value yields no directive (the block[0]
# mirror-the-input fallback applies).
_UI_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "zh-TW": "Traditional Chinese",
}


def render_user_language(user_jwt: str) -> str:
    """Return the per-user reply-language directive for the prompt tail.

    Reads `users_meta.ui_language` under the caller's JWT (RLS scopes it to
    the user). Returns a one-line directive Claude follows for its prose
    reply language (chat_v12, DESIGN.md §6.6 Tier 2), or "" when the user
    has no explicit language set — in which case block[0]'s instruction to
    mirror the user's input language applies.

    Output shape, set case:
        The user's interface language is Japanese (ja). Reply in Japanese.

    Output shape, unset case:
        "" (empty)

    Defensive: any read error returns "" so a transient DB issue degrades to
    the mirror-the-input fallback rather than failing the chat turn.
    """
    try:
        client = supabase_for_user(user_jwt)
        rows = (
            client.table("users_meta")
            .select("ui_language")
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        return ""
    code = rows[0].get("ui_language") if rows else None
    name = _UI_LANGUAGE_NAMES.get(code or "")
    if not name:
        return ""
    return f"The user's interface language is {name} ({code}). Reply in {name}."


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
