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
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from anthropic import Anthropic
from supabase import Client

# DESIGN.md §6.4 — v1 hardcodes ET; per-user timezone is Phase 2.
_DIGEST_TZ = ZoneInfo("America/New_York")
_BASELINE_WEEKS = 8

# Sonnet model is resolved from env per CLAUDE.md "Model usage by task"
# invariant: model strings are environment-resolved so we can roll a
# model forward or back without a code change. Mirrors the chat agent's
# ANTHROPIC_MODEL pattern but kept separate so the chat downgrade to
# Haiku doesn't drag the digest down with it. v1 default is
# claude-sonnet-4-6 per the same table in CLAUDE.md.
_DEFAULT_DIGEST_MODEL = "claude-sonnet-4-6"
_SONNET_PROMPT_VERSION = "digest_v1"


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
    'usual pattern").'
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
class DigestPayload:
    """The aggregates that drive both the email body and the Sonnet call.

    Decimal everywhere — never float (CLAUDE.md invariant 13 / DESIGN.md
    §8.2). Top-category may be None for a user who somehow has no
    transactions in the past week despite the eligibility filter (race
    against a same-day soft-delete).
    """
    user_id: UUID
    week_start: datetime          # Monday 00:00 ET of the week being summarized
    week_end: datetime            # Sunday 23:59:59 ET of the same week
    week_total: Decimal
    baseline_avg: Decimal         # 8-week trailing avg, excludes the just-ended week
    top_category: CategoryRollup | None
    home_currency: str
    observation: str              # Sonnet output
    nudge: str | None             # Sonnet output


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
    week_start, week_end = _previous_week_bounds_et()
    baseline_start = week_start - timedelta(weeks=_BASELINE_WEEKS)

    home_currency = _read_home_currency(client, user_id)
    transactions = _read_active_transactions(client, user_id, baseline_start, week_end)

    week_total, baseline_avg, top_category = _aggregate(
        transactions, week_start=week_start, week_end=week_end
    )

    observation, nudge, sonnet_call_log = _call_sonnet(
        anthropic_client=anthropic_client,
        week_total=week_total,
        baseline_avg=baseline_avg,
        top_category=top_category,
        home_currency=home_currency,
    )

    payload = DigestPayload(
        user_id=user_id,
        week_start=week_start,
        week_end=week_end,
        week_total=week_total,
        baseline_avg=baseline_avg,
        top_category=top_category,
        home_currency=home_currency,
        observation=observation,
        nudge=nudge,
    )
    return payload, sonnet_call_log


def render_email(payload: DigestPayload, unsubscribe_url: str) -> RenderedEmail:
    """Render the digest into HTML + plaintext bodies (≤5 content blocks each).

    Inline styles only — Gmail strips `<style>` blocks and class names.
    The visible Unsubscribe link in the body is required even though
    the List-Unsubscribe header carries the same URL: the header is
    what Gmail/Yahoo's "Unsubscribe" button hits; the body link is what
    users actually find when they want out.
    """
    week_label = payload.week_start.strftime("%b %-d") + "–" + payload.week_end.strftime("%-d")
    delta = payload.week_total - payload.baseline_avg
    delta_word = "above" if delta > 0 else ("below" if delta < 0 else "in line with")
    money = lambda d: _format_money(d, payload.home_currency)

    # Block 1: total vs baseline. Block 2: top category vs its baseline.
    # Block 3: observation. Block 4 (conditional): nudge. Block 5: unsub.
    line_total = (
        f"You spent {money(payload.week_total)} last week — "
        f"{money(abs(delta))} {delta_word} your weekly average."
    )
    if payload.top_category is not None:
        cat = payload.top_category
        cat_delta = cat.week_total - cat.baseline_avg
        cat_word = "above" if cat_delta > 0 else ("below" if cat_delta < 0 else "in line with")
        line_top = (
            f"Top category: {cat.category} at {money(cat.week_total)} — "
            f"{money(abs(cat_delta))} {cat_word} its 8-week baseline."
        )
    else:
        line_top = "No category stood out this week."

    line_observation = payload.observation
    line_nudge = payload.nudge  # may be None

    subject = f"Tameru — week of {week_label}"

    # Plaintext: one block per line, blank lines between.
    text_blocks = [line_total, line_top, line_observation]
    if line_nudge:
        text_blocks.append(line_nudge)
    text_blocks.append(f"Unsubscribe: {unsubscribe_url}")
    text = "\n\n".join(text_blocks) + "\n"

    # HTML: tight inline-styled paragraphs. No <style> block, no class
    # names (Gmail strips both). Sans-serif system stack avoids a web
    # font dependency. Color tokens are flat hex (not Tameru's CSS vars
    # — Gmail doesn't resolve those).
    p_style = (
        "margin:0 0 12px 0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"
        "'Segoe UI',sans-serif;color:#1a1a1a;"
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
    parts.append(
        f'<p style="{unsub_style}">'
        f'<a href="{_html_escape(unsubscribe_url)}" style="color:#888;">Unsubscribe</a>'
        f"</p>"
    )
    html = (
        '<div style="max-width:480px;margin:0 auto;padding:24px;'
        'background:#fafafa;">'
        + "".join(parts)
        + "</div>"
    )

    return RenderedEmail(subject=subject, html=html, text=text)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _previous_week_bounds_et() -> tuple[datetime, datetime]:
    """Return Mon 00:00 ET → Sun 23:59:59.999999 ET of the previous week.

    Computed against `now()` in ET, walked back to the Monday on or
    before today, then minus one week.
    """
    now_et = datetime.now(_DIGEST_TZ)
    today_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_this_week = today_et - timedelta(days=today_et.weekday())
    week_start = monday_this_week - timedelta(days=7)
    week_end = monday_this_week - timedelta(microseconds=1)
    return week_start, week_end


def _read_home_currency(client: Client, user_id: UUID) -> str:
    """Read `users_meta.home_currency` for `user_id`. Default USD if missing."""
    resp = (
        client.table("users_meta")
        .select("home_currency")
        .eq("user_id", str(user_id))
        .execute()
    )
    if resp.data:
        return resp.data[0].get("home_currency") or "USD"
    return "USD"


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
) -> tuple[str, str | None, SonnetCallLog]:
    """Ask Sonnet for {observation, nudge}. Send aggregates only.

    Returns (observation, nudge, call_log) — the call_log is what the
    cron caller passes into its admin `ai_call_log` write.
    """
    client = anthropic_client or Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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
        return _fallback_narrative(week_total, baseline_avg), None, call_log

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
        observation = _fallback_narrative(week_total, baseline_avg)
        nudge = None

    if not observation:
        observation = _fallback_narrative(week_total, baseline_avg)

    call_log = SonnetCallLog(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        success=True,
        error_code=None,
    )
    return observation, nudge, call_log


def _fallback_narrative(week_total: Decimal, baseline_avg: Decimal) -> str:
    """Deterministic, ungenerated observation when Sonnet fails or misbehaves.

    The digest is more valuable than the narrative — shipping a quiet
    factual line beats burying the user's actual numbers behind a
    silent abort.
    """
    if baseline_avg == 0:
        return "First week of tracked spending — your baseline starts here."
    delta = week_total - baseline_avg
    if abs(delta) < baseline_avg * Decimal("0.10"):
        return "Spending was steady this week, in line with your usual pattern."
    direction = "higher" if delta > 0 else "lower"
    return f"Spending ran {direction} than your 8-week average this week."


def _format_money(value: Decimal, currency: str) -> str:
    """Render `value` in `currency` as `$1,234.56` (or the currency-coded form)."""
    quantized = value.quantize(Decimal("0.01"))
    if currency == "USD":
        return f"${quantized:,.2f}"
    return f"{currency} {quantized:,.2f}"


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
