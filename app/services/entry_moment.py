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
  3. **cumulative_delta**      — MTD spend pulling above the 3-month avg.
  4. **card_mismatch**         — wrong-card usage with a better option.

Rate-limit windows live in `entry_moment_fires`. They are enforced by
`entry_moment_signals(p_transaction_id)` returning the most recent fire
timestamp per rule within its window; this function treats any non-null
timestamp as a hard suppression.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.models.transactions import TransactionRow

RULE_SINGLE_TX_NOTABLE = "single_tx_notable"
RULE_WEEKLY_FREQUENCY = "weekly_frequency"
RULE_CUMULATIVE_DELTA = "cumulative_delta"
RULE_CARD_MISMATCH = "card_mismatch"

MIN_TX_COUNT_FOR_BASELINE = 6
MIN_HISTORY_DAYS_FOR_BASELINE = 30
NOISE_PERCENT_FLOOR = Decimal("0.10")
RULE_2_MIN_THIS_WEEK_COUNT = 3
RULE_2_MIN_PRIOR_AVG = Decimal("0.5")
RULE_1_MIN_MONTH_COUNT = 3
RULE_4_MIN_WRONG_CARD_COUNT = 2


def entry_moment_insight(
    user: AuthedUser, transaction: TransactionRow
) -> str | None:
    """Return a one-sentence insight for the just-committed transaction, or None.

    Request:
        user: the authenticated caller (JWT-scoped for all DB reads).
        transaction: the row that `POST /transactions/confirm` just inserted.

    Response:
        A short prose sentence (str) when a rule fires, or `None` when no
        rule applies. The caller passes the value straight through to
        `TransactionConfirmResponse.insight`.

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
    return decision.sentence


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

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "_Signals":
        """Coerce a raw RPC row into a typed `_Signals`."""
        return cls(
            user_id=UUID(row["user_id"]),
            category=row["txn_category"],
            amount=Decimal(str(row["txn_amount"] or 0)),
            card_id=UUID(row["txn_card_id"]) if row.get("txn_card_id") else None,
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
        )


@dataclass(frozen=True)
class _RuleDecision:
    """One picked rule + the sentence it generated + the category to record."""

    rule_id: str
    sentence: str
    category: str


def _pick_rule(sig: _Signals) -> _RuleDecision | None:
    """Evaluate rules in priority order, returning the first eligible one.

    The order weights useful-once over useful-repeatedly so the bubble
    doesn't fatigue. Within each rule, the eligibility checks combine:
      * a "context" guard (does the user have enough history to make
        the sentence honest?),
      * a "signal" guard (is the situation actually noteworthy?), and
      * a rate-limit check (has the same rule already fired in its
        suppression window?).
    """
    decision = _rule_single_tx_notable(sig)
    if decision is not None:
        return decision
    decision = _rule_weekly_frequency(sig)
    if decision is not None:
        return decision
    decision = _rule_cumulative_delta(sig)
    if decision is not None:
        return decision
    decision = _rule_card_mismatch(sig)
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
        rule_id=RULE_SINGLE_TX_NOTABLE, sentence=sentence, category=sig.category
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
        rule_id=RULE_WEEKLY_FREQUENCY, sentence=sentence, category=sig.category
    )


def _rule_cumulative_delta(sig: _Signals) -> _RuleDecision | None:
    """Fire when MTD category spend is pulling notably above the baseline.

    Guards:
      * Category cleared the soft gate.
      * `mtd > baseline` by both 10% and the absolute floor.
      * `days_remaining > 0` — phrasing assumes some month is left.
      * Rate limit: once per category per rolling 7 days.
      * Suppressed when rule 2 already fired this week for this category
        (they answer adjacent questions; firing both is noisy).
    """
    if not _passes_soft_gate(sig):
        return None
    if sig.monthly_baseline_category <= 0:
        return None
    delta = sig.mtd_category_spend - sig.monthly_baseline_category
    if not _passes_noise_threshold(delta, sig.monthly_baseline_category):
        return None
    if delta <= 0:
        return None
    if sig.days_remaining_in_month <= 0:
        return None
    if sig.last_cumulative_delta_at is not None:
        return None
    if sig.last_weekly_frequency_at is not None:
        return None
    delta_int = int(round(float(delta)))
    sentence = (
        f"this puts you ${delta_int} above your monthly {sig.category.lower()} "
        f"average with {sig.days_remaining_in_month} days left."
    )
    return _RuleDecision(
        rule_id=RULE_CUMULATIVE_DELTA, sentence=sentence, category=sig.category
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
        rule_id=RULE_CARD_MISMATCH, sentence=sentence, category=sig.category
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
