"""Entry-moment insight rule engine — Day 13 (DESIGN.md §6.2).

`entry_moment_insight(user, transaction)` is called by
`POST /transactions/confirm` after a non-replayed insert. It evaluates four
deterministic rules in priority order and returns either:

  * a one-sentence chat-bubble string (rule fired), or
  * `None` (no rule applicable — noise, missing baseline, or saturated
    rate limit).

The function is **deterministic, not pure**: it reads `transactions`,
`cards`, and `entry_moment_fires` through the user's JWT (one RPC call)
and writes one fire row when a rule applies (one upsert call). "No model
variance" — not "no I/O" (Day 13 prompt).

The four rules, highest priority first (rationale in the Day 13 prompt):

  1. **single_tx_notable**     — new monthly high in a category.
  2. **weekly_frequency**      — elevated weekly count vs the 4-week avg.
  3. **cumulative_delta**      — category spend (projected or current)
                                 pulling above the baseline.
  4. **card_mismatch**         — wrong-card usage with a better option.

Each fired insight carries a `severity` tier — `calm` / `elevated` /
`alert` — that drives the frontend bubble's visual weight (DESIGN.md §6.2).
Only the pace-aware rule 3 escalates above `calm`.

Rate-limit windows live in `entry_moment_fires`. They are enforced by
`entry_moment_signals(p_transaction_id)` returning the most recent fire
timestamp per rule within its window; this function treats any non-null
timestamp as a hard suppression.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.models.transactions import EntryMomentInsight, TransactionRow

RULE_SINGLE_TX_NOTABLE = "single_tx_notable"
RULE_WEEKLY_FREQUENCY = "weekly_frequency"
RULE_CUMULATIVE_DELTA = "cumulative_delta"
RULE_CARD_MISMATCH = "card_mismatch"
RULE_CATEGORY_SHARE = "category_share"
RULE_LARGEST_THIS_WEEK = "largest_this_week"
RULE_PACING_UNDER = "pacing_under"

MIN_TX_COUNT_FOR_BASELINE = 6
MIN_HISTORY_DAYS_FOR_BASELINE = 30
NOISE_PERCENT_FLOOR = Decimal("0.10")
RULE_2_MIN_THIS_WEEK_COUNT = 3
RULE_2_MIN_PRIOR_AVG = Decimal("0.5")
RULE_1_MIN_MONTH_COUNT = 3
RULE_4_MIN_WRONG_CARD_COUNT = 2

# Warm-up rules (category_share, largest_this_week) — gate-free observations
# that give a first-month user something honest to see before the soft gate
# clears. They make no baseline claim, so they don't need history.
RULE_5_MIN_PRIOR_MONTH_COUNT = 2  # ≥2 prior in-category ⇒ fires by the 3rd.
RULE_5_MIN_SHARE = Decimal("0.35")  # this purchase ≥35% of the month's category.
RULE_6_MIN_WEEK_COUNT = 3  # a "biggest this week" needs a few to rank against.

# Rule 3 (cumulative_delta) — pace projection + severity banding.
# Below this many days into the month a straight-line projection from
# 2-3 days of data is noise, so rule 3 falls back to retrospective framing.
RULE_3_MIN_DAYS_FOR_PROJECTION = 5
SEVERITY_ALERT_RATIO = Decimal("0.25")

# Rule 7 (pacing_under) — the positive counterpart to rule 3. Forecast-only
# (a "you're under" claim in the first days of the month is meaningless) and
# a wider percent floor than the over-baseline noise floor, so it fires only
# when the user is *comfortably* under — a genuine "you're okay" moment.
RULE_7_UNDER_RATIO = Decimal("0.15")

# Severity tiers — drive EntryInsightBubble's tiered visual treatment and
# mirror the §6.3 dashboard color scale. `calm` is the quiet grey aside;
# `positive` is green (comfortably below baseline); `elevated` is amber
# (10-25% over baseline); `alert` is terracotta (25%+).
SEVERITY_CALM = "calm"
SEVERITY_POSITIVE = "positive"
SEVERITY_ELEVATED = "elevated"
SEVERITY_ALERT = "alert"


def entry_moment_insight(
    user: AuthedUser, transaction: TransactionRow
) -> EntryMomentInsight | None:
    """Return an entry-moment insight for the just-committed transaction, or None.

    Request:
        user: the authenticated caller (JWT-scoped for all DB reads).
        transaction: the row that `POST /transactions/confirm` just inserted.

    Response:
        An `EntryMomentInsight` (one prose sentence + a `severity` tier)
        when a rule fires, or `None` when no rule applies. The caller passes
        the value straight through to `TransactionConfirmResponse.insight`.

    Side effect: on a non-`None` return, inserts one row into
    `entry_moment_fires` so the rate-limit windows in `entry_moment_signals`
    suppress the same rule from re-firing within its TTL.
    """
    client = supabase_for_user(user.jwt)
    resp = client.rpc(
        "entry_moment_signals", {"p_transaction_id": str(transaction.id)}
    ).execute()
    rows: list[dict[str, Any]] = resp.data or []
    if not rows:
        return None

    signals = _Signals.from_row(rows[0])
    decision = _pick_rule(signals)
    if decision is None:
        return None

    _record_fire(user, decision.rule_id, decision.category)
    return EntryMomentInsight(text=decision.sentence, severity=decision.severity)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Signals:
    """Typed view of one `entry_moment_signals` RPC row.

    The RPC returns JSON, so numerics come back as strings (Decimal-safe)
    and ints come back as Python ints. This dataclass coerces them once
    and exposes them with stable names so rule evaluation stays readable.
    """

    user_id: UUID
    category: str
    amount: Decimal
    card_id: UUID | None
    txn_date: date
    category_tx_count_prior: int
    category_history_days: int
    month_tx_count_in_category: int
    prior_max_in_category_this_month: Decimal
    this_week_count: int
    prior_4w_avg_weekly_count: Decimal
    mtd_category_spend: Decimal
    monthly_baseline_category: Decimal
    days_remaining_in_month: int
    this_card_name: str | None
    this_card_multiplier: Decimal
    best_card_name: str | None
    best_card_multiplier: Decimal
    wrong_card_count_this_week: int
    last_single_tx_notable_at: str | None
    last_weekly_frequency_at: str | None
    last_cumulative_delta_at: str | None
    last_card_mismatch_at: str | None
    week_tx_count_all: int
    week_prior_max_all: Decimal
    last_category_share_at: str | None
    last_largest_this_week_at: str | None
    last_pacing_under_at: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "_Signals":
        """Coerce a raw RPC row into a typed `_Signals`."""
        return cls(
            user_id=UUID(row["user_id"]),
            category=row["txn_category"],
            amount=Decimal(str(row["txn_amount"] or 0)),
            card_id=UUID(row["txn_card_id"]) if row.get("txn_card_id") else None,
            txn_date=date.fromisoformat(str(row["txn_date"])),
            category_tx_count_prior=int(row.get("category_tx_count_prior") or 0),
            category_history_days=int(row.get("category_history_days") or 0),
            month_tx_count_in_category=int(row.get("month_tx_count_in_category") or 0),
            prior_max_in_category_this_month=Decimal(
                str(row.get("prior_max_in_category_this_month") or 0)
            ),
            this_week_count=int(row.get("this_week_count") or 0),
            prior_4w_avg_weekly_count=Decimal(
                str(row.get("prior_4w_avg_weekly_count") or 0)
            ),
            mtd_category_spend=Decimal(str(row.get("mtd_category_spend") or 0)),
            monthly_baseline_category=Decimal(
                str(row.get("monthly_baseline_category") or 0)
            ),
            days_remaining_in_month=int(row.get("days_remaining_in_month") or 0),
            this_card_name=row.get("this_card_name"),
            this_card_multiplier=Decimal(str(row.get("this_card_multiplier") or 1)),
            best_card_name=row.get("best_card_name"),
            best_card_multiplier=Decimal(str(row.get("best_card_multiplier") or 0)),
            wrong_card_count_this_week=int(row.get("wrong_card_count_this_week") or 0),
            last_single_tx_notable_at=row.get("last_single_tx_notable_at"),
            last_weekly_frequency_at=row.get("last_weekly_frequency_at"),
            last_cumulative_delta_at=row.get("last_cumulative_delta_at"),
            last_card_mismatch_at=row.get("last_card_mismatch_at"),
            week_tx_count_all=int(row.get("week_tx_count_all") or 0),
            week_prior_max_all=Decimal(str(row.get("week_prior_max_all") or 0)),
            last_category_share_at=row.get("last_category_share_at"),
            last_largest_this_week_at=row.get("last_largest_this_week_at"),
            last_pacing_under_at=row.get("last_pacing_under_at"),
        )


@dataclass(frozen=True)
class _RuleDecision:
    """One picked rule: the sentence, the category to record, the severity tier."""

    rule_id: str
    sentence: str
    category: str
    severity: str


def _pick_rule(sig: _Signals) -> _RuleDecision | None:
    """Evaluate rules in priority order, returning the first eligible one.

    The order weights useful-once over useful-repeatedly so the bubble
    doesn't fatigue. Within each rule, the eligibility checks combine:
      * a "context" guard (does the user have enough history to make
        the sentence honest?),
      * a "signal" guard (is the situation actually noteworthy?), and
      * a rate-limit check (has the same rule already fired in its
        suppression window?).

    The pace-aware overspending rule (rule 3) is evaluated up front: an
    `alert`-tier result — spend tracking 25%+ over the category baseline —
    is the loudest, most actionable thing the bubble can say, so it
    outranks the calmer "new monthly high" (rule 1) and "elevated weekly
    frequency" (rule 2) observations. Below the alert band, rule 3 keeps
    its original priority-3 slot.

    The gate-free warm-up rules (category_share, largest_this_week) and the
    positive rule (pacing_under) sit *below* every "real signal" so they
    only surface when nothing louder applies. This keeps them from ever
    talking over an overspend or card-mismatch nudge, while still giving a
    first-month user (whose soft-gated rules can't fire yet) and an on-track
    user something worth seeing.
    """
    overspending = _rule_cumulative_delta(sig)
    if overspending is not None and overspending.severity == SEVERITY_ALERT:
        return overspending

    decision = _rule_single_tx_notable(sig)
    if decision is not None:
        return decision
    decision = _rule_weekly_frequency(sig)
    if decision is not None:
        return decision
    if overspending is not None:
        return overspending
    decision = _rule_card_mismatch(sig)
    if decision is not None:
        return decision
    decision = _rule_category_share(sig)
    if decision is not None:
        return decision
    decision = _rule_largest_this_week(sig)
    if decision is not None:
        return decision
    decision = _rule_pacing_under(sig)
    if decision is not None:
        return decision
    return None


def _rule_single_tx_notable(sig: _Signals) -> _RuleDecision | None:
    """Fire when this transaction is a new monthly high in its category.

    Guards:
      * At least `RULE_1_MIN_MONTH_COUNT` prior in-month transactions —
        without prior context "highest" is meaningless.
      * `amount > prior_max` (strict) — ties don't count.
      * Rate limit: once per category per calendar month.
    """
    if sig.month_tx_count_in_category < RULE_1_MIN_MONTH_COUNT:
        return None
    if sig.amount <= sig.prior_max_in_category_this_month:
        return None
    if sig.last_single_tx_notable_at is not None:
        return None
    sentence = f"highest single {sig.category.lower()} spend this month."
    return _RuleDecision(
        rule_id=RULE_SINGLE_TX_NOTABLE,
        sentence=sentence,
        category=sig.category,
        severity=SEVERITY_CALM,
    )


def _rule_weekly_frequency(sig: _Signals) -> _RuleDecision | None:
    """Fire when this week's category count is elevated vs the 4-week avg.

    Guards:
      * Category has cleared the soft new-user gate (≥6 prior tx and ≥30
        days history) — otherwise "you usually have N" is fabricated.
      * `this_week_count >= 3` and `this_week_count >= 2 * prior_avg`.
      * `prior_avg >= 0.5` (about 2 in the prior 4 weeks) — keeps the
        comparison meaningful.
      * Rate limit: once per category per rolling 7 days.
    """
    if not _passes_soft_gate(sig):
        return None
    if sig.this_week_count < RULE_2_MIN_THIS_WEEK_COUNT:
        return None
    if sig.prior_4w_avg_weekly_count < RULE_2_MIN_PRIOR_AVG:
        return None
    if Decimal(sig.this_week_count) < Decimal(2) * sig.prior_4w_avg_weekly_count:
        return None
    if sig.last_weekly_frequency_at is not None:
        return None
    avg_int = max(1, int(round(float(sig.prior_4w_avg_weekly_count))))
    sentence = (
        f"{_ordinal(sig.this_week_count)} {sig.category.lower()} transaction "
        f"this week — you usually have {avg_int}."
    )
    return _RuleDecision(
        rule_id=RULE_WEEKLY_FREQUENCY,
        sentence=sentence,
        category=sig.category,
        severity=SEVERITY_CALM,
    )


def _rule_cumulative_delta(sig: _Signals) -> _RuleDecision | None:
    """Fire when category spend — projected or current — pulls above baseline.

    This is the only rule that escalates above `calm`. It frames the
    overspending signal by how much of the month has elapsed:

      * **Forecast** (default, `RULE_3_MIN_DAYS_FOR_PROJECTION` days into
        the month or later) — straight-lines month-to-date spend to a
        month-end projection and compares that to the baseline. Forward-
        looking: "on pace for about $N over your monthly dining average."
      * **Retrospective** (the first few days of the month, where a
        projection from 2-3 days of data is noise) — compares month-to-date
        spend directly. "this puts you $N above ... with M days left."

    Severity is the §6.3 band of the delta (projected or current): 10-25%
    over baseline → `elevated`, 25%+ → `alert` (see `_delta_severity`).

    Guards:
      * Category cleared the soft gate.
      * `days_remaining > 0` — phrasing assumes some month is left.
      * The delta clears the combined percent + absolute noise floor.
      * Rate limit: once per category per rolling 7 days.
      * Suppressed when rule 2 already fired this week for this category
        (they answer adjacent questions; firing both is noisy).
    """
    if not _passes_soft_gate(sig):
        return None
    if sig.monthly_baseline_category <= 0:
        return None
    if sig.days_remaining_in_month <= 0:
        return None
    if sig.last_cumulative_delta_at is not None:
        return None
    if sig.last_weekly_frequency_at is not None:
        return None

    days_elapsed = sig.txn_date.day
    use_forecast = days_elapsed >= RULE_3_MIN_DAYS_FOR_PROJECTION
    if use_forecast:
        days_in_month = days_elapsed + sig.days_remaining_in_month
        projected = (
            sig.mtd_category_spend
            / Decimal(days_elapsed)
            * Decimal(days_in_month)
        )
        delta = projected - sig.monthly_baseline_category
    else:
        delta = sig.mtd_category_spend - sig.monthly_baseline_category

    if delta <= 0:
        return None
    if not _passes_noise_threshold(delta, sig.monthly_baseline_category):
        return None

    delta_int = int(round(float(delta)))
    if use_forecast:
        sentence = (
            f"on pace for about ${delta_int} over your monthly "
            f"{sig.category.lower()} average."
        )
    else:
        sentence = (
            f"this puts you ${delta_int} above your monthly "
            f"{sig.category.lower()} average with "
            f"{sig.days_remaining_in_month} days left."
        )
    return _RuleDecision(
        rule_id=RULE_CUMULATIVE_DELTA,
        sentence=sentence,
        category=sig.category,
        severity=_delta_severity(delta, sig.monthly_baseline_category),
    )


def _rule_card_mismatch(sig: _Signals) -> _RuleDecision | None:
    """Fire when the user used a sub-best card and there's a better option.

    Guards:
      * Both this card and a best card resolved.
      * Best card's multiplier strictly above this card's.
      * `wrong_card_count_this_week >= RULE_4_MIN_WRONG_CARD_COUNT` —
        don't bug the user the first time.
      * Rate limit: once across all categories per rolling 14 days.
    """
    if sig.this_card_name is None or sig.best_card_name is None:
        return None
    if sig.best_card_multiplier <= sig.this_card_multiplier:
        return None
    if sig.wrong_card_count_this_week < RULE_4_MIN_WRONG_CARD_COUNT:
        return None
    if sig.last_card_mismatch_at is not None:
        return None
    multiplier = _format_multiplier(sig.best_card_multiplier)
    sentence = (
        f"you've used {sig.this_card_name} for {sig.category.lower()} "
        f"{sig.wrong_card_count_this_week} times this week — "
        f"{sig.best_card_name} earns {multiplier}x there."
    )
    return _RuleDecision(
        rule_id=RULE_CARD_MISMATCH,
        sentence=sentence,
        category=sig.category,
        severity=SEVERITY_CALM,
    )


def _rule_category_share(sig: _Signals) -> _RuleDecision | None:
    """Fire when this purchase is a large share of the month's category spend.

    A gate-free "where's it going" observation — it claims no baseline, so it
    needs no history and can surface in a user's first month.

    Guards:
      * At least `RULE_5_MIN_PRIOR_MONTH_COUNT` prior in-month transactions in
        the category (so "X% of your dining this month" ranks against a few,
        not one).
      * This purchase is `RULE_5_MIN_SHARE`–100% of month-to-date category
        spend (the upper bound skips refund-distorted months where the ratio
        exceeds 1).
      * Rate limit: once per category per rolling 7 days.
    """
    if sig.month_tx_count_in_category < RULE_5_MIN_PRIOR_MONTH_COUNT:
        return None
    if sig.mtd_category_spend <= 0:
        return None
    share = sig.amount / sig.mtd_category_spend
    if share < RULE_5_MIN_SHARE or share > 1:
        return None
    if sig.last_category_share_at is not None:
        return None
    pct = int(round(float(share) * 100))
    sentence = f"that's {pct}% of your {sig.category.lower()} spending this month."
    return _RuleDecision(
        rule_id=RULE_CATEGORY_SHARE,
        sentence=sentence,
        category=sig.category,
        severity=SEVERITY_CALM,
    )


def _rule_largest_this_week(sig: _Signals) -> _RuleDecision | None:
    """Fire when this is the biggest single purchase this week, across categories.

    A gate-free cross-category "notable" — the weekly cousin of rule 1's
    per-category monthly high. Needs no baseline, so it surfaces early.

    Guards:
      * At least `RULE_6_MIN_WEEK_COUNT` transactions this week (including this
        one) — otherwise "biggest this week" is trivially true.
      * `amount > week_prior_max_all` (strict) — strictly larger than every
        other purchase in the 7-day window.
      * Rate limit: once per rolling 7 days (across all categories).
    """
    if sig.week_tx_count_all < RULE_6_MIN_WEEK_COUNT:
        return None
    if sig.amount <= sig.week_prior_max_all:
        return None
    if sig.last_largest_this_week_at is not None:
        return None
    return _RuleDecision(
        rule_id=RULE_LARGEST_THIS_WEEK,
        sentence="biggest single purchase this week.",
        category=sig.category,
        severity=SEVERITY_CALM,
    )


def _rule_pacing_under(sig: _Signals) -> _RuleDecision | None:
    """Fire when projected category spend is comfortably under the baseline.

    The positive counterpart to rule 3 (cumulative_delta): same forecast
    machinery, opposite sign. Forecast-only — a "you're under" claim in the
    first days of a month is meaningless — and requires the soft gate, because
    it *does* assert a personal baseline. The wider percent floor
    (`RULE_7_UNDER_RATIO`) keeps it to genuinely-on-track moments so it reads as
    a warm "you're okay," not a constant.

    Guards:
      * Category cleared the soft gate.
      * `days_remaining > 0` and at least `RULE_3_MIN_DAYS_FOR_PROJECTION` days
        elapsed (so the forecast is honest).
      * Projected spend is under baseline by the combined absolute + percent
        floor.
      * Rate limit: once per category per rolling 14 days.
    """
    if not _passes_soft_gate(sig):
        return None
    if sig.monthly_baseline_category <= 0:
        return None
    if sig.days_remaining_in_month <= 0:
        return None
    if sig.last_pacing_under_at is not None:
        return None

    days_elapsed = sig.txn_date.day
    if days_elapsed < RULE_3_MIN_DAYS_FOR_PROJECTION:
        return None
    days_in_month = days_elapsed + sig.days_remaining_in_month
    projected = (
        sig.mtd_category_spend / Decimal(days_elapsed) * Decimal(days_in_month)
    )
    delta = sig.monthly_baseline_category - projected  # positive ⇒ under budget
    if delta <= 0:
        return None
    if delta < _min_delta_usd():
        return None
    if delta / sig.monthly_baseline_category < RULE_7_UNDER_RATIO:
        return None

    delta_int = int(round(float(delta)))
    sentence = (
        f"on pace for about ${delta_int} under your monthly "
        f"{sig.category.lower()} average."
    )
    return _RuleDecision(
        rule_id=RULE_PACING_UNDER,
        sentence=sentence,
        category=sig.category,
        severity=SEVERITY_POSITIVE,
    )


def _passes_soft_gate(sig: _Signals) -> bool:
    """Return True iff the category has enough history for a real baseline.

    Mirrors the dashboard's soft gate so a category that doesn't earn a
    "real" baseline tile on the dashboard also doesn't fire baseline-
    dependent rules (2 and 3) in the chat bubble.
    """
    return (
        sig.category_tx_count_prior >= MIN_TX_COUNT_FOR_BASELINE
        and sig.category_history_days >= MIN_HISTORY_DAYS_FOR_BASELINE
    )


def _delta_severity(delta: Decimal, baseline: Decimal) -> str:
    """Map an over-baseline delta onto the §6.3 amber/red severity bands.

    A delta reaching this function has already cleared the 10% noise floor
    (`_passes_noise_threshold`), so it is at least `elevated`; the only
    question is whether it has crossed the 25% line into `alert`. Mirrors
    the dashboard category tile's color scale so the entry-moment bubble
    and the dashboard speak one visual language.
    """
    if baseline <= 0:
        return SEVERITY_ELEVATED
    if delta / baseline >= SEVERITY_ALERT_RATIO:
        return SEVERITY_ALERT
    return SEVERITY_ELEVATED


def _passes_noise_threshold(delta: Decimal, baseline: Decimal) -> bool:
    """Combined percent-and-absolute-floor noise filter.

    A 10%-of-baseline-only filter misweights small-baseline categories
    (10% of a $50 groceries baseline is $5, which is noise). The
    absolute floor is environment-configurable via
    `ENTRY_MOMENT_MIN_DELTA_USD` (default 10) so the threshold can be
    tuned post-launch without code change.
    """
    if baseline <= 0:
        return False
    abs_delta = abs(delta)
    if abs_delta < _min_delta_usd():
        return False
    if abs_delta / baseline < NOISE_PERCENT_FLOOR:
        return False
    return True


def _min_delta_usd() -> Decimal:
    """Read the absolute-floor threshold from env, defaulting to $10."""
    raw = os.environ.get("ENTRY_MOMENT_MIN_DELTA_USD")
    if raw is None:
        return Decimal(10)
    try:
        return Decimal(raw)
    except Exception:
        return Decimal(10)


def _ordinal(n: int) -> str:
    """Format a positive integer with its English ordinal suffix.

    1→"1st", 2→"2nd", 3→"3rd", 11/12/13→"th" (the irregular teens),
    21→"21st", etc.
    """
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_multiplier(value: Decimal) -> str:
    """Render a card multiplier as a compact string ("4" not "4.00", "1.5" preserved).

    Card multipliers in the seed data are typically integers (1, 2, 4)
    but some programs use fractional values (1.5, 2.5). Strip a trailing
    ".0" so the sentence reads naturally without truncating real fractions.
    """
    quantized = value.normalize()
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _record_fire(user: AuthedUser, rule_id: str, category: str) -> None:
    """Insert one row into `entry_moment_fires` for rate-limit accounting.

    Uses the user's JWT so the INSERT policy on the table (`user_id =
    auth.uid()`) fires. We never write on the idempotent-replay path —
    that branch in `routes/transactions.py` never calls this service.
    """
    client = supabase_for_user(user.jwt)
    client.table("entry_moment_fires").insert(
        {
            "user_id": str(user.user_id),
            "rule_id": rule_id,
            "category": category,
        }
    ).execute()
