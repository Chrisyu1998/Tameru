"""Compose and render the weekly spending digest (DESIGN.md §6.4).

Pure data + rendering. The cron entry point in `app/cron/digest.py` owns
the service-role Supabase client, the per-user iteration, the
`email_log` write, and the AI-call logging. This module is invoked from
that loop and receives the admin client as a parameter — it does NOT
import `supabase_admin` (the service-role-leak test enforces this).

PRIVACY BOUNDARY: Sonnet receives ONLY aggregates — category totals,
week-over-week deltas. No merchant names, no raw transaction rows.
Anthropic has ZDR but minimum-surface is the rule (CLAUDE.md privacy
posture). The Sonnet output is constrained to a 2-field JSON
({observation, nudge}), with explicit length and tone constraints in
the system prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from anthropic import Anthropic
from supabase import Client

from app.prompts.categories import category_display_label

# DESIGN.md §6.4 / §6.6 — the digest runs in each user's own timezone
# (`users_meta.timezone`). This is the fallback when a user has no zone set
# (NULL column) or a legacy/invalid value: the historical ET behavior, so
# pre-Day-29 users are unaffected. The cron imports the *name* for its
# per-user send-window gate.
DEFAULT_DIGEST_TZ_NAME = "America/New_York"
_DEFAULT_DIGEST_TZ = ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
_BASELINE_WEEKS = 8

# Phase-2 credit tracking (§6.7). N = 14, not 7: the digest sends weekly, so a
# 7-day window catches exactly one send (one reminder, maybe only a day before
# reset), while 14 catches two (earliest 8-14 days out — real lead time + a
# follow-up). Cadence-aware tuning is a deferred refinement (TODO.md).
_CREDIT_EXPIRY_WINDOW_DAYS = 14
# Cap the "expiring soon" list so the email stays tight; soonest-first, so a
# user tracking many credits sees the most urgent.
_CREDIT_EXPIRY_MAX_LINES = 3

# Sonnet model is resolved from env per CLAUDE.md "Model usage by task"
# invariant: model strings are environment-resolved so we can roll a
# model forward or back without a code change. Mirrors the chat agent's
# ANTHROPIC_MODEL pattern but kept separate so the chat downgrade to
# Haiku doesn't drag the digest down with it. v1 default is
# claude-sonnet-4-6 per the same table in CLAUDE.md.
_DEFAULT_DIGEST_MODEL = "claude-sonnet-4-6"
# digest_v2 (Day 29 Tier 2) — the narrative is now written in the user's
# ui_language (DESIGN.md §6.6). The static system prompt gained one
# language-awareness sentence; the target language name is passed per-call in
# the user message (kept out of the static prompt so the hash is language-
# independent). Bumped from digest_v1 so ai_call_log.prompt_hash lines up.
_SONNET_PROMPT_VERSION = "digest_v2"

# Human language names for the supported ui_language codes, used in the Sonnet
# user message and as a fallback-narrative key. NULL/unknown → English.
_UI_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "zh-TW": "Traditional Chinese",
}


def digest_model() -> str:
    """Resolve the digest model from env, falling back to the v1 default.

    Public (no underscore) because the cron reads it for the
    `ai_call_log` payload and the helper-position doctrine doesn't
    apply to functions referenced by other primary definitions —
    memory.md 2026-05-20 "execution-order-forced helpers must drop the
    underscore." Returns the override env when set; otherwise the
    sentinel default `claude-sonnet-4-6` per CLAUDE.md.
    """
    return os.environ.get("ANTHROPIC_DIGEST_MODEL") or _DEFAULT_DIGEST_MODEL
_SONNET_SYSTEM_PROMPT = (
    "You write one short factual observation about a user's weekly spending, "
    "plus one optional nudge. Output JSON only: "
    '{"observation": "...", "nudge": null | "..."}. Both strings must be '
    "<=100 characters, matter-of-fact in tone, no exclamation marks, no "
    "second-person imperatives. Set nudge to null when no actionable nudge "
    "applies (most weeks). Never mention specific merchants or dollar amounts "
    "the user didn't already see in the digest. If spending is unremarkable, "
    'say so plainly (e.g. "spending was steady this week, in line with your '
    'usual pattern"). Write the observation and nudge in the language named '
    "in the user message; the <=100-character limit still applies to that "
    "language's text."
)


@dataclass(frozen=True)
class CategoryRollup:
    """One category's week and baseline."""
    category: str
    week_total: Decimal
    baseline_avg: Decimal


@dataclass(frozen=True)
class SonnetCallLog:
    """Metadata the cron writes into `ai_call_log` for the digest's Sonnet call.

    `compose_digest` returns this alongside the payload; the cron then
    calls its admin `ai_call_log` helper with these fields plus the
    provider/model/task_type constants. Keeping the shape tight (no
    free-form `extra` dict) means the digest's audit row is identical
    to every other AI call site.
    """
    input_tokens: int
    output_tokens: int
    latency_ms: int
    success: bool
    error_code: str | None


@dataclass(frozen=True)
class ExpiringCredit:
    """One statement credit near its reset with unused headroom (Phase 2, §6.7).

    Surfaced in the digest's "use it or lose it" line. Only credits with a
    known `amount` and `used_amount < amount` reach this — an unknown allowance
    can't produce a "$X unused" figure. `days_left` is clamped `>= 0`.
    """
    name: str
    amount: Decimal
    used_amount: Decimal
    next_reset_date: date
    days_left: int


@dataclass(frozen=True)
class CreditValueCaptured:
    """Wallet-wide credit utilization this period (Phase 2, §6.7).

    `used` and `available` are summed across active credits with a known
    allowance (`used` capped at each credit's allowance). Drives the positive
    "you've captured $X of $Y" digest line. None when the user tracks no
    amount-bearing credits.
    """
    used: Decimal
    available: Decimal


@dataclass(frozen=True)
class DigestPayload:
    """The aggregates that drive both the email body and the Sonnet call.

    Decimal everywhere — never float (CLAUDE.md invariant 13 / DESIGN.md
    §8.2). Top-category may be None for a user who somehow has no
    transactions in the past week despite the eligibility filter (race
    against a same-day soft-delete).
    """
    user_id: UUID
    week_start: datetime          # Monday 00:00 in the user's tz, week summarized
    week_end: datetime            # Sunday 23:59:59 in the user's tz, same week
    week_total: Decimal
    baseline_avg: Decimal         # 8-week trailing avg, excludes the just-ended week
    top_category: CategoryRollup | None
    home_currency: str
    observation: str              # Sonnet output (in ui_language)
    nudge: str | None             # Sonnet output (in ui_language)
    # en | ja | zh-TW | None — drives the email chrome + narrative language.
    # Last with a default so callers/tests predating Tier 2 still construct a
    # valid (English) payload; compose_digest always passes it explicitly.
    ui_language: str | None = None
    # Phase-2 credit tracking (§6.7). Defaulted so pre-Phase-2 callers/tests
    # still build a valid payload; the in-app recap (which reuses this payload)
    # ignores them — only render_email surfaces the credit lines.
    expiring_credits: tuple[ExpiringCredit, ...] = ()
    value_captured: CreditValueCaptured | None = None


@dataclass(frozen=True)
class RenderedEmail:
    """HTML + plaintext body, ready for the Resend wrapper."""
    subject: str
    html: str
    text: str


def compose_digest(
    client: Client,
    user_id: UUID,
    *,
    anthropic_client: Anthropic | None = None,
) -> tuple[DigestPayload, "SonnetCallLog"]:
    """Build the per-user digest aggregates and Sonnet narrative.

    Returns the payload plus the Sonnet call metadata the cron uses to
    write `ai_call_log`. We return them together rather than via a
    side channel so the contract is testable and thread-safe.

    The Sonnet call is the one external network round-trip in this
    function; pass `anthropic_client` to inject a fake in tests. The
    Sonnet call is logged to `ai_call_log` *by the caller*
    (`app/cron/digest.py`) so this module stays free of service-role
    imports.

    Raises if the underlying queries fail; the cron loop catches the
    exception, logs it via the JSON logger, writes an `email_log` row
    with success=False, and moves to the next user.
    """
    home_currency, tz, ui_language = _read_user_settings(client, user_id)
    week_start, week_end = _previous_week_bounds(tz)
    baseline_start = week_start - timedelta(weeks=_BASELINE_WEEKS)

    transactions = _read_active_transactions(client, user_id, baseline_start, week_end)

    week_total, baseline_avg, top_category = _aggregate(
        transactions, week_start=week_start, week_end=week_end
    )

    # Phase-2 credit tracking (§6.7): expiring-soon + value-captured, computed
    # against NOW in the user's zone (not the summarized week — expiry is a
    # forward-looking "act before the reset" signal). Cheap DB read, no LLM.
    expiring_credits, value_captured = _read_credit_status(client, user_id, tz)

    observation, nudge, sonnet_call_log = _call_sonnet(
        anthropic_client=anthropic_client,
        week_total=week_total,
        baseline_avg=baseline_avg,
        top_category=top_category,
        home_currency=home_currency,
        ui_language=ui_language,
    )

    payload = DigestPayload(
        user_id=user_id,
        week_start=week_start,
        week_end=week_end,
        week_total=week_total,
        baseline_avg=baseline_avg,
        top_category=top_category,
        home_currency=home_currency,
        ui_language=ui_language,
        observation=observation,
        nudge=nudge,
        expiring_credits=expiring_credits,
        value_captured=value_captured,
    )
    return payload, sonnet_call_log


def render_email(
    payload: DigestPayload,
    unsubscribe_url: str,
    *,
    app_cta_url: str,
) -> RenderedEmail:
    """Render the digest into HTML + plaintext bodies (≤5 content blocks each).

    Inline styles only — Gmail strips `<style>` blocks and class names.
    The visible Unsubscribe link in the body is required even though
    the List-Unsubscribe header carries the same URL: the header is
    what Gmail/Yahoo's "Unsubscribe" button hits; the body link is what
    users actually find when they want out.

    `app_cta_url` is the "View this week in Tameru" CTA target — the
    Vercel PWA host with `?source=digest` appended (Day 26b). Required
    kwarg: omitting it raises `TypeError`. The CTA is a navigation
    affordance, NOT one of the ≤5 prose content blocks.

    All visible copy is localized to `payload.ui_language` (DESIGN.md §6.6
    Tier 2); the Sonnet `observation`/`nudge` already arrive in that language.
    NULL/unknown language → English (the pre-Tier-2 copy).
    """
    s = _DIGEST_STRINGS.get(payload.ui_language or "en", _DIGEST_STRINGS["en"])
    week_label = _week_label(payload.week_start, payload.week_end, payload.ui_language)
    delta = payload.week_total - payload.baseline_avg
    money = lambda d: _format_money(d, payload.home_currency)

    # Block 1: total vs baseline. Block 2: top category vs its baseline.
    # Block 3: observation. Block 4 (conditional): nudge. Block 5: unsub.
    # Direction picks a full localized template so each language reads
    # naturally (grammar differs — "above" isn't a drop-in across languages).
    total_key = "total_above" if delta > 0 else ("total_below" if delta < 0 else "total_inline")
    line_total = s[total_key].format(total=money(payload.week_total), delta=money(abs(delta)))
    if payload.top_category is not None:
        cat = payload.top_category
        cat_delta = cat.week_total - cat.baseline_avg
        top_key = "top_above" if cat_delta > 0 else ("top_below" if cat_delta < 0 else "top_inline")
        line_top = s[top_key].format(
            cat=category_display_label(cat.category, payload.ui_language),
            amt=money(cat.week_total),
            delta=money(abs(cat_delta)),
        )
    else:
        line_top = s["no_top"]

    line_observation = payload.observation
    line_nudge = payload.nudge  # may be None

    # Phase-2 credit sections (§6.7): each is a group of plain lines rendered
    # as one block (blank line around it in text, one <p> in HTML). The
    # expiring group leads with a bold heading; value-captured is a lone line.
    # Credit names are user data, so the HTML path escapes every line.
    credit_sections: list[list[str]] = []
    if payload.expiring_credits:
        section = [s["credits_heading"]]
        for c in payload.expiring_credits:
            section.append(
                s["credit_expiring"].format(
                    name=c.name,
                    remaining=money(c.amount - c.used_amount),
                    amount=money(c.amount),
                    days=c.days_left,
                )
            )
        credit_sections.append(section)
    if payload.value_captured is not None:
        v = payload.value_captured
        credit_sections.append(
            [s["value_captured"].format(used=money(v.used), available=money(v.available))]
        )

    subject = s["subject"].format(week=week_label)

    # Plaintext: one block per line, blank lines between. The CTA line
    # sits above Unsubscribe — closer to the prose, separate from the
    # housekeeping footer.
    text_blocks = [line_total, line_top, line_observation]
    if line_nudge:
        text_blocks.append(line_nudge)
    for section in credit_sections:
        text_blocks.append("\n".join(section))
    text_blocks.append(f"{s['cta']}: {app_cta_url}")
    text_blocks.append(f"{s['unsub']}: {unsubscribe_url}")
    text = "\n\n".join(text_blocks) + "\n"

    # HTML: tight inline-styled paragraphs. No <style> block, no class
    # names (Gmail strips both). Sans-serif system stack avoids a web
    # font dependency. Color tokens are flat hex (not Tameru's CSS vars
    # — Gmail doesn't resolve those).
    p_style = (
        "margin:0 0 12px 0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"
        "'Segoe UI',sans-serif;color:#1a1a1a;"
    )
    # Inline-styled pill button. Class names are stripped by Gmail, so
    # every visual property lives on the `style` attribute. Single
    # dark-on-light treatment to match the digest's restrained tone.
    cta_wrap_style = "margin:24px 0 0 0;"
    cta_link_style = (
        "display:inline-block;padding:10px 20px;background:#1a1a1a;"
        "color:#fafafa;text-decoration:none;border-radius:8px;"
        "font:14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    )
    unsub_style = (
        "margin:24px 0 0 0;font:12px/1.5 -apple-system,BlinkMacSystemFont,"
        "'Segoe UI',sans-serif;color:#888;"
    )
    parts = [
        f'<p style="{p_style}">{_html_escape(line_total)}</p>',
        f'<p style="{p_style}">{_html_escape(line_top)}</p>',
        f'<p style="{p_style}">{_html_escape(line_observation)}</p>',
    ]
    if line_nudge:
        parts.append(f'<p style="{p_style}">{_html_escape(line_nudge)}</p>')
    # Phase-2 credit sections: expiring group leads with a bold heading; a
    # lone value-captured line is not bolded. Every line escaped (user data).
    for section in credit_sections:
        if len(section) > 1:
            head = f"<strong>{_html_escape(section[0])}</strong>"
            body = "".join(f"<br>{_html_escape(line)}" for line in section[1:])
            parts.append(f'<p style="{p_style}">{head}{body}</p>')
        else:
            parts.append(f'<p style="{p_style}">{_html_escape(section[0])}</p>')
    # CTA sits after the prose, before the unsubscribe footer — Day 26b.
    parts.append(
        f'<p style="{cta_wrap_style}">'
        f'<a href="{_html_escape(app_cta_url)}" style="{cta_link_style}">'
        f"{_html_escape(s['cta'])}"
        f"</a>"
        f"</p>"
    )
    parts.append(
        f'<p style="{unsub_style}">'
        f'<a href="{_html_escape(unsubscribe_url)}" style="color:#888;">'
        f"{_html_escape(s['unsub'])}</a>"
        f"</p>"
    )
    html = (
        '<div style="max-width:480px;margin:0 auto;padding:24px;'
        'background:#fafafa;">'
        + "".join(parts)
        + "</div>"
    )

    return RenderedEmail(subject=subject, html=html, text=text)


def local_week_monday(tz: ZoneInfo, now: datetime) -> date:
    """The Monday (date) of `now`'s week in `tz` — the digest/recap dedup key.

    Shared by the digest cron (`_local_week_monday`, which resolves the zone
    name and stringifies the result) and the in-app recap route (GET
    /chat/recap), so both compute the same `weekly_recap.dedup_week` for a
    given user and week. Invariant across the Monday-morning retry fires and
    across a mid-week timezone change (memory 2026-06-01).
    """
    local = now.astimezone(tz)
    return (local - timedelta(days=local.weekday())).date()


def recap_row(payload: DigestPayload, dedup_week: date) -> dict[str, Any]:
    """Build the `weekly_recap` insert row from a composed digest payload.

    Pure (no DB). Reused by both write paths so the stored row shape is
    identical regardless of trigger: the cron upserts this under the service
    role; GET /chat/recap upserts it under the user's JWT. Decimals are
    stringified for the numeric columns (never float — invariant 13). The
    same dict also feeds the route's response builder, so a freshly-composed
    recap and a re-read stored recap render byte-identically.
    """
    top = payload.top_category
    return {
        "user_id": str(payload.user_id),
        "dedup_week": dedup_week.isoformat(),
        "week_start": payload.week_start.date().isoformat(),
        "week_end": payload.week_end.date().isoformat(),
        "week_total": str(payload.week_total),
        "baseline_avg": str(payload.baseline_avg),
        "top_category": top.category if top is not None else None,
        "top_category_total": str(top.week_total) if top is not None else None,
        "top_category_baseline": str(top.baseline_avg) if top is not None else None,
        "home_currency": payload.home_currency,
        "ui_language": payload.ui_language,
        "observation": payload.observation,
        "nudge": payload.nudge,
    }


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _previous_week_bounds(tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return Mon 00:00 → Sun 23:59:59.999999 of the previous week, in `tz`.

    Computed against `now()` in the user's timezone, walked back to the
    Monday on or before today, then minus one week. Doing the boundary math
    in the user's own zone means a transaction near Sunday/Monday midnight
    is bucketed into the week the user experienced it, not a UTC- or
    ET-shifted one (DESIGN.md §6.6).
    """
    now_local = datetime.now(tz)
    today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_this_week = today_local - timedelta(days=today_local.weekday())
    week_start = monday_this_week - timedelta(days=7)
    week_end = monday_this_week - timedelta(microseconds=1)
    return week_start, week_end


def _read_user_settings(
    client: Client, user_id: UUID
) -> tuple[str, ZoneInfo, str | None]:
    """Read `(home_currency, timezone, ui_language)` for `user_id`.

    Response shape: `(currency, ZoneInfo, ui_language)`. Defaults to
    `("USD", DEFAULT_DIGEST_TZ, None)` when no row exists; an absent or
    unresolvable `timezone` (NULL column, or a legacy value not in this
    Python's tz database) falls back to the default zone, and a NULL
    `ui_language` falls back to English in the renderer — so the digest
    never crashes on a missing value.
    """
    resp = (
        client.table("users_meta")
        .select("home_currency, timezone, ui_language")
        .eq("user_id", str(user_id))
        .execute()
    )
    if not resp.data:
        return "USD", _DEFAULT_DIGEST_TZ, None
    row = resp.data[0]
    home_currency = row.get("home_currency") or "USD"
    return home_currency, _resolve_tz(row.get("timezone")), row.get("ui_language")


def _resolve_tz(name: str | None) -> ZoneInfo:
    """Resolve an IANA zone name to a `ZoneInfo`, falling back to the default.

    Defensive: stored zones are validated at write time (app/util/timezone),
    but NULL and any legacy/invalid value must still produce a usable zone
    rather than raise inside the cron loop.
    """
    if not name:
        return _DEFAULT_DIGEST_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return _DEFAULT_DIGEST_TZ


def _read_active_transactions(
    client: Client, user_id: UUID, range_start: datetime, range_end: datetime
) -> list[dict[str, Any]]:
    """Read `active` transactions in the half-open range `[start, end]`.

    Returned shape is the minimum needed for aggregation: `date`,
    `amount`, `category`. Excludes refunds (amount < 0) at the
    aggregation layer, not here.
    """
    resp = (
        client.table("transactions")
        .select("date,amount,category")
        .eq("user_id", str(user_id))
        .eq("status", "active")
        .gte("date", range_start.date().isoformat())
        .lte("date", range_end.date().isoformat())
        .execute()
    )
    return resp.data or []


def _read_credit_status(
    client: Client, user_id: UUID, tz: ZoneInfo
) -> tuple[tuple[ExpiringCredit, ...], CreditValueCaptured | None]:
    """Read the user's active credits → (expiring-soon list, value captured).

    Works under both callers' clients: the cron's service-role admin client
    (where `.eq("user_id", …)` is the scope) and the recap route's user JWT
    (where RLS already scopes it, and the filter is redundant-but-correct).

    Expiring = a known allowance, `used_amount < amount`, and `next_reset_date`
    within `_CREDIT_EXPIRY_WINDOW_DAYS` of today-in-`tz`, soonest first. Value
    captured sums `used` (capped at each allowance) vs total allowance across
    amount-bearing credits; None when the user tracks none.
    """
    today = datetime.now(tz).date()
    resp = (
        client.table("card_credits")
        .select("name,amount,used_amount,next_reset_date")
        .eq("user_id", str(user_id))
        .eq("status", "active")
        .execute()
    )
    expiring: list[ExpiringCredit] = []
    total_used = Decimal("0")
    total_available = Decimal("0")
    for row in resp.data or []:
        if row.get("amount") is None:
            continue  # no allowance → no "unused" figure, no ratio contribution
        amount = Decimal(str(row["amount"]))
        used = Decimal(str(row["used_amount"]))
        total_available += amount
        total_used += min(used, amount)
        if used >= amount:
            continue  # fully captured — nothing to remind about
        days_left = (date.fromisoformat(row["next_reset_date"]) - today).days
        if days_left <= _CREDIT_EXPIRY_WINDOW_DAYS:
            expiring.append(
                ExpiringCredit(
                    name=row["name"],
                    amount=amount,
                    used_amount=used,
                    next_reset_date=date.fromisoformat(row["next_reset_date"]),
                    days_left=max(0, days_left),
                )
            )
    expiring.sort(key=lambda e: e.days_left)
    value = (
        CreditValueCaptured(used=total_used, available=total_available)
        if total_available > 0
        else None
    )
    return tuple(expiring[:_CREDIT_EXPIRY_MAX_LINES]), value


def _aggregate(
    transactions: list[dict[str, Any]],
    *,
    week_start: datetime,
    week_end: datetime,
) -> tuple[Decimal, Decimal, CategoryRollup | None]:
    """Compute week total + 8-week trailing avg + top-category rollup.

    Refunds (negative amounts) are subtracted at the row level — the
    digest reports *net* spend per the dashboard convention. The
    baseline average excludes the just-ended week so a high-spend week
    doesn't pull its own comparison up.
    """
    week_start_date = week_start.date()
    week_end_date = week_end.date()

    week_rows: list[tuple[str, Decimal]] = []
    baseline_totals: dict[int, Decimal] = {}

    for row in transactions:
        amount = Decimal(str(row["amount"]))
        date_str = row["date"]
        tx_date = datetime.fromisoformat(date_str).date()
        category = row.get("category") or "Other"
        if week_start_date <= tx_date <= week_end_date:
            week_rows.append((category, amount))
            continue
        # Bucket pre-week rows by which 7-day window from week_start they
        # fall into (negative = earlier weeks).
        days_before = (week_start_date - tx_date).days
        week_idx = days_before // 7  # 0 = the week immediately before
        baseline_totals[week_idx] = baseline_totals.get(week_idx, Decimal("0")) + amount

    week_total = sum((amt for _cat, amt in week_rows), Decimal("0"))

    if baseline_totals:
        baseline_avg = sum(baseline_totals.values(), Decimal("0")) / Decimal(_BASELINE_WEEKS)
    else:
        baseline_avg = Decimal("0")

    # Top category by week total. None if the week has no rows.
    cat_totals: dict[str, Decimal] = {}
    for cat, amt in week_rows:
        cat_totals[cat] = cat_totals.get(cat, Decimal("0")) + amt
    top_category: CategoryRollup | None = None
    if cat_totals:
        top_name = max(cat_totals, key=lambda c: cat_totals[c])
        top_week = cat_totals[top_name]
        # Compute the same category's 8-week baseline avg from baseline rows.
        cat_baseline_per_week: dict[int, Decimal] = {}
        for row in transactions:
            tx_date = datetime.fromisoformat(row["date"]).date()
            if week_start_date <= tx_date <= week_end_date:
                continue
            if (row.get("category") or "Other") != top_name:
                continue
            week_idx = (week_start_date - tx_date).days // 7
            cat_baseline_per_week[week_idx] = cat_baseline_per_week.get(week_idx, Decimal("0")) + Decimal(str(row["amount"]))
        if cat_baseline_per_week:
            cat_baseline_avg = sum(cat_baseline_per_week.values(), Decimal("0")) / Decimal(_BASELINE_WEEKS)
        else:
            cat_baseline_avg = Decimal("0")
        top_category = CategoryRollup(
            category=top_name,
            week_total=top_week,
            baseline_avg=cat_baseline_avg,
        )

    return week_total, baseline_avg, top_category


def _call_sonnet(
    *,
    anthropic_client: Anthropic | None,
    week_total: Decimal,
    baseline_avg: Decimal,
    top_category: CategoryRollup | None,
    home_currency: str,
    ui_language: str | None,
) -> tuple[str, str | None, SonnetCallLog]:
    """Ask Sonnet for {observation, nudge}. Send aggregates only.

    Returns (observation, nudge, call_log) — the call_log is what the
    cron caller passes into its admin `ai_call_log` write. `ui_language`
    selects the prose language (DESIGN.md §6.6 Tier 2); NULL/unknown → English.
    """
    client = anthropic_client or Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    language_name = _UI_LANGUAGE_NAMES.get(ui_language or "en", "English")

    user_payload = {
        "week_total": str(week_total),
        "baseline_avg": str(baseline_avg),
        "home_currency": home_currency,
        "top_category": (
            {
                "name": top_category.category,
                "week_total": str(top_category.week_total),
                "baseline_avg": str(top_category.baseline_avg),
            }
            if top_category is not None
            else None
        ),
    }
    user_message = (
        f"Write the observation and nudge in {language_name}.\n\n"
        "Here are this week's spending aggregates (no merchant names, "
        "no transaction list — aggregates only):\n"
        + json.dumps(user_payload, indent=2)
    )

    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=digest_model(),
            max_tokens=200,
            system=_SONNET_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        call_log = SonnetCallLog(
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            success=False,
            error_code=type(exc).__name__,
        )
        # Fallback narrative so the email still ships if Sonnet 5xx's.
        return _fallback_narrative(week_total, baseline_avg, ui_language), None, call_log

    latency_ms = int((time.perf_counter() - started) * 1000)
    text_block = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"),
        "",
    )
    try:
        parsed = json.loads(text_block)
        observation = str(parsed.get("observation") or "").strip()[:100]
        nudge_value = parsed.get("nudge")
        nudge = str(nudge_value).strip()[:100] if nudge_value else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        # Sonnet returned non-JSON; fall back rather than ship the raw blob.
        observation = _fallback_narrative(week_total, baseline_avg, ui_language)
        nudge = None

    if not observation:
        observation = _fallback_narrative(week_total, baseline_avg, ui_language)

    call_log = SonnetCallLog(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        success=True,
        error_code=None,
    )
    return observation, nudge, call_log


def _fallback_narrative(
    week_total: Decimal, baseline_avg: Decimal, ui_language: str | None = None
) -> str:
    """Deterministic, ungenerated observation when Sonnet fails or misbehaves.

    The digest is more valuable than the narrative — shipping a quiet
    factual line beats burying the user's actual numbers behind a
    silent abort. Localized to `ui_language` (DESIGN.md §6.6 Tier 2) so a
    Sonnet outage still ships a same-language digest; NULL/unknown → English.
    """
    strings = _FALLBACK_STRINGS.get(ui_language or "en", _FALLBACK_STRINGS["en"])
    if baseline_avg == 0:
        return strings["first_week"]
    delta = week_total - baseline_avg
    if abs(delta) < baseline_avg * Decimal("0.10"):
        return strings["steady"]
    return strings["higher"] if delta > 0 else strings["lower"]


# Localized fallback observations (DESIGN.md §6.6 Tier 2). Keys mirror the
# branches in `_fallback_narrative`. `en` preserves the pre-Tier-2 copy.
_FALLBACK_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "first_week": "First week of tracked spending — your baseline starts here.",
        "steady": "Spending was steady this week, in line with your usual pattern.",
        "higher": "Spending ran higher than your 8-week average this week.",
        "lower": "Spending ran lower than your 8-week average this week.",
    },
    "ja": {
        "first_week": "支出トラッキングの最初の週です。ここから基準ができていきます。",
        "steady": "今週の支出はいつものパターンとほぼ同じで、安定していました。",
        "higher": "今週の支出は8週間の平均を上回りました。",
        "lower": "今週の支出は8週間の平均を下回りました。",
    },
    "zh-TW": {
        "first_week": "這是開始記錄支出的第一週，基準從這裡建立。",
        "steady": "本週支出穩定，與平常的模式相當。",
        "higher": "本週支出高於 8 週平均。",
        "lower": "本週支出低於 8 週平均。",
    },
}


# Localized email chrome (DESIGN.md §6.6 Tier 2). One full template per delta
# direction so each language reads naturally — "above"/"below" are not drop-in
# swappable across grammars. `{total}`/`{delta}`/`{amt}` are pre-formatted
# money strings; `{cat}` is a localized category label; `{week}` is the week
# label. `en` preserves the pre-Tier-2 copy verbatim. Drafts — native speakers
# refine. Mirrors frontend digest-adjacent copy conventions (§6.4).
_DIGEST_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "subject": "Tameru — week of {week}",
        "total_above": "You spent {total} last week — {delta} above your weekly average.",
        "total_below": "You spent {total} last week — {delta} below your weekly average.",
        "total_inline": "You spent {total} last week — in line with your weekly average.",
        "top_above": "Top category: {cat} at {amt} — {delta} above its 8-week baseline.",
        "top_below": "Top category: {cat} at {amt} — {delta} below its 8-week baseline.",
        "top_inline": "Top category: {cat} at {amt} — in line with its 8-week baseline.",
        "no_top": "No category stood out this week.",
        "credits_heading": "Use it or lose it:",
        "credit_expiring": "{name}: {remaining} of {amount} unused, resets in {days}d.",
        "value_captured": "You've captured {used} of {available} in card credits this period.",
        "cta": "View this week in Tameru",
        "unsub": "Unsubscribe",
    },
    "ja": {
        "subject": "Tameru — {week} の振り返り",
        "total_above": "先週の支出は {total} でした。週平均を {delta} 上回っています。",
        "total_below": "先週の支出は {total} でした。週平均を {delta} 下回っています。",
        "total_inline": "先週の支出は {total} でした。週平均とほぼ同じです。",
        "top_above": "最も多かったカテゴリー：{cat}（{amt}）。8週間の平均を {delta} 上回っています。",
        "top_below": "最も多かったカテゴリー：{cat}（{amt}）。8週間の平均を {delta} 下回っています。",
        "top_inline": "最も多かったカテゴリー：{cat}（{amt}）。8週間の平均とほぼ同じです。",
        "no_top": "今週は特に目立ったカテゴリーはありませんでした。",
        "credits_heading": "使わないと失効する特典：",
        "credit_expiring": "{name}：{amount} のうち {remaining} 未使用、あと {days}日でリセット。",
        "value_captured": "今期はカード特典 {available} のうち {used} を活用しました。",
        "cta": "Tameru で今週を見る",
        "unsub": "配信停止",
    },
    "zh-TW": {
        "subject": "Tameru — {week} 週回顧",
        "total_above": "上週支出 {total}，比每週平均多 {delta}。",
        "total_below": "上週支出 {total}，比每週平均少 {delta}。",
        "total_inline": "上週支出 {total}，與每週平均相當。",
        "top_above": "最高類別：{cat}，{amt}，比 8 週平均多 {delta}。",
        "top_below": "最高類別：{cat}，{amt}，比 8 週平均少 {delta}。",
        "top_inline": "最高類別：{cat}，{amt}，與 8 週平均相當。",
        "no_top": "本週沒有特別突出的類別。",
        "credits_heading": "快到期的回饋，別忘了使用：",
        "credit_expiring": "{name}：{amount} 中還有 {remaining} 未使用，{days} 天後重置。",
        "value_captured": "本期已使用卡片回饋 {available} 中的 {used}。",
        "cta": "在 Tameru 查看本週",
        "unsub": "取消訂閱",
    },
}


def _week_label(week_start: datetime, week_end: datetime, ui_language: str | None) -> str:
    """Localized week-range label for the subject/body.

    en preserves the pre-Tier-2 "Apr 7–13" shape; ja/zh-TW use the
    month-day form natural to those locales and always name both months so a
    week spanning a month boundary stays unambiguous. NULL/unknown → English.
    """
    if ui_language == "ja":
        return (
            f"{week_start.month}月{week_start.day}日"
            f"〜{week_end.month}月{week_end.day}日"
        )
    if ui_language == "zh-TW":
        return (
            f"{week_start.month}月{week_start.day}日"
            f"–{week_end.month}月{week_end.day}日"
        )
    return week_start.strftime("%b %-d") + "–" + week_end.strftime("%-d")


# Per-currency symbol + fraction digits for the digest email (DESIGN.md §6.6).
# The email is rendered server-side in Python, which has no `Intl`, so the
# frontend's `Intl.NumberFormat(..., currencyDisplay:'narrowSymbol')` can't be
# reused — this map mirrors its intent for the nine `home_currency` CHECK-set
# currencies. JPY is the only zero-decimal currency in that set, so it's the
# only entry that drops the fractional part (¥1,500, not ¥1,500.00).
_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
    "CHF": "CHF ",
    "SGD": "S$",
    "TWD": "NT$",
}
_ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset({"JPY"})


def _format_money(value: Decimal, currency: str) -> str:
    """Render `value` in `currency` with the right symbol and decimal places.

    `¥1,500` for JPY (zero-decimal), `$1,234.56` for USD, `NT$500.00` for TWD,
    `CHF 1,234.56` for Swiss francs. An unknown currency (defensive — the
    `home_currency` CHECK set is fixed) falls back to a `CODE 1,234.56` prefix.
    """
    decimals = 0 if currency in _ZERO_DECIMAL_CURRENCIES else 2
    quantized = value.quantize(Decimal(1) if decimals == 0 else Decimal("0.01"))
    symbol = _CURRENCY_SYMBOLS.get(currency)
    if symbol is None:
        return f"{currency} {quantized:,.{decimals}f}"
    return f"{symbol}{quantized:,.{decimals}f}"


def _html_escape(text: str) -> str:
    """Minimal HTML escape — Resend does not auto-escape body content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _prompt_hash() -> str:
    """Return the SHA-256 hex of the rendered system prompt.

    Exposed for the cron's `ai_call_log` write (prompt-version invariant).
    """
    return hashlib.sha256(_SONNET_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# Public surface the cron's ai_call_log payload reads. Keep
# `digest_model` (the function defined above) accessible by name —
# it's a function, not a constant, so the cron picks up env changes
# at call time without a redeploy.
SONNET_PROMPT_VERSION = _SONNET_PROMPT_VERSION
sonnet_prompt_hash = _prompt_hash
