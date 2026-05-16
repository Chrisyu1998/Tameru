"""Unit tests for the entry-moment rule engine — Day 13.

`_pick_rule(signals)` is the deterministic core of `entry_moment_insight`.
It reads pre-aggregated signals from one `entry_moment_signals(...)` RPC
row and returns either a `_RuleDecision` (rule fired, with sentence) or
`None` (no rule applicable).

These tests synthesize signals directly — no Supabase, no RPC. The SQL
function is exercised end-to-end by `tests/routes/test_transactions.py`
and `tests/routes/test_dashboard.py` once the integration suite runs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.services.entry_moment import (
    MIN_HISTORY_DAYS_FOR_BASELINE,
    MIN_TX_COUNT_FOR_BASELINE,
    NOISE_PERCENT_FLOOR,
    RULE_1_MIN_MONTH_COUNT,
    RULE_2_MIN_PRIOR_AVG,
    RULE_2_MIN_THIS_WEEK_COUNT,
    RULE_4_MIN_WRONG_CARD_COUNT,
    RULE_CARD_MISMATCH,
    RULE_CUMULATIVE_DELTA,
    RULE_SINGLE_TX_NOTABLE,
    RULE_WEEKLY_FREQUENCY,
    _format_multiplier,
    _ordinal,
    _passes_noise_threshold,
    _pick_rule,
    _Signals,
)


# ---------------------------------------------------------------------------
# _pick_rule — rule 1 (single-tx notable)
# ---------------------------------------------------------------------------


def test_rule_1_fires_when_new_monthly_max_with_enough_context():
    """Rule 1 fires when the just-committed tx strictly beats the prior monthly max."""
    sig = _signals(
        amount=Decimal("80"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT,
        prior_max_in_category_this_month=Decimal("50"),
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_SINGLE_TX_NOTABLE
    assert decision.sentence == "highest single dining spend this month."


def test_rule_1_skipped_when_under_min_month_count():
    """Rule 1 needs ≥3 prior in-month transactions for 'highest' to be meaningful."""
    sig = _signals(
        amount=Decimal("80"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT - 1,
        prior_max_in_category_this_month=Decimal("50"),
    )
    assert _pick_rule(sig) is None


def test_rule_1_skipped_when_tie_with_prior_max():
    """Rule 1 requires strict greater than — equal amount does not fire."""
    sig = _signals(
        amount=Decimal("50"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT,
        prior_max_in_category_this_month=Decimal("50"),
    )
    assert _pick_rule(sig) is None


def test_rule_1_suppressed_by_recent_fire():
    """Rule 1 honors the once-per-category-per-month rate limit."""
    sig = _signals(
        amount=Decimal("80"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT,
        prior_max_in_category_this_month=Decimal("50"),
        last_single_tx_notable_at="2026-05-10T00:00:00Z",
    )
    assert _pick_rule(sig) is None


# ---------------------------------------------------------------------------
# _pick_rule — rule 2 (weekly frequency)
# ---------------------------------------------------------------------------


def test_rule_2_fires_with_elevated_week_against_meaningful_prior_avg():
    """Rule 2 fires when this week is ≥2× the prior 4-week weekly average."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        this_week_count=4,
        prior_4w_avg_weekly_count=Decimal("2"),
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_WEEKLY_FREQUENCY
    assert decision.sentence == "4th dining transaction this week — you usually have 2."


def test_rule_2_skipped_when_under_soft_gate():
    """Without ≥6 prior tx AND ≥30 days history, baseline-dependent rules don't fire."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE - 1,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        this_week_count=4,
        prior_4w_avg_weekly_count=Decimal("2"),
    )
    assert _pick_rule(sig) is None


def test_rule_2_skipped_when_prior_avg_too_low():
    """Rule 2 needs a prior 4-week average ≥ RULE_2_MIN_PRIOR_AVG to be meaningful."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        this_week_count=5,
        prior_4w_avg_weekly_count=RULE_2_MIN_PRIOR_AVG - Decimal("0.1"),
    )
    assert _pick_rule(sig) is None


def test_rule_2_skipped_when_under_min_count_this_week():
    """Rule 2 needs at least RULE_2_MIN_THIS_WEEK_COUNT transactions this week."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        this_week_count=RULE_2_MIN_THIS_WEEK_COUNT - 1,
        prior_4w_avg_weekly_count=Decimal("1"),
    )
    assert _pick_rule(sig) is None


# ---------------------------------------------------------------------------
# _pick_rule — rule 3 (cumulative delta)
# ---------------------------------------------------------------------------


def test_rule_3_fires_when_mtd_above_baseline_past_noise_floor():
    """Rule 3 fires when MTD exceeds baseline by >10% AND >$10."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("200"),
        monthly_baseline_category=Decimal("150"),
        days_remaining_in_month=12,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA
    assert "above your monthly dining average" in decision.sentence
    assert "12 days left" in decision.sentence


def test_rule_3_suppressed_when_rule_2_already_fired_this_week():
    """Rule 3 backs off when rule 2 already fired in the same category this week."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        # Rule 2's signals do NOT meet — to isolate the suppression check.
        this_week_count=0,
        prior_4w_avg_weekly_count=Decimal("0"),
        mtd_category_spend=Decimal("200"),
        monthly_baseline_category=Decimal("150"),
        days_remaining_in_month=12,
        last_weekly_frequency_at="2026-05-12T00:00:00Z",
    )
    assert _pick_rule(sig) is None


def test_rule_3_skipped_when_delta_below_absolute_floor():
    """Even a percent-passing delta below the $10 absolute floor is treated as noise."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("55"),
        monthly_baseline_category=Decimal("50"),
        days_remaining_in_month=12,
    )
    # +10% over $50 = $5 delta — passes percent but below absolute floor.
    assert _pick_rule(sig) is None


def test_rule_3_skipped_when_delta_below_percent_floor():
    """Large absolute delta with small percent (e.g., $11 over $400 baseline) is noise."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("411"),
        monthly_baseline_category=Decimal("400"),
        days_remaining_in_month=12,
    )
    # $11 / $400 = 2.75% — below the 10% floor.
    assert _pick_rule(sig) is None


def test_rule_3_skipped_on_last_day_of_month():
    """Rule 3's sentence assumes 'with N days left' — last day is no-op."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("200"),
        monthly_baseline_category=Decimal("150"),
        days_remaining_in_month=0,
    )
    assert _pick_rule(sig) is None


# ---------------------------------------------------------------------------
# _pick_rule — rule 4 (card mismatch)
# ---------------------------------------------------------------------------


def test_rule_4_fires_when_better_card_exists_and_repeat_use():
    """Rule 4 fires only on repeat wrong-card usage with a strictly-better option."""
    sig = _signals(
        this_card_name="Chase Freedom",
        this_card_multiplier=Decimal("1"),
        best_card_name="Amex Gold",
        best_card_multiplier=Decimal("4"),
        wrong_card_count_this_week=RULE_4_MIN_WRONG_CARD_COUNT,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CARD_MISMATCH
    assert "Chase Freedom" in decision.sentence
    assert "Amex Gold" in decision.sentence
    assert "4x" in decision.sentence


def test_rule_4_skipped_under_min_wrong_card_count():
    """First-occurrence wrong-card use shouldn't trigger — only repeats."""
    sig = _signals(
        this_card_name="Chase Freedom",
        this_card_multiplier=Decimal("1"),
        best_card_name="Amex Gold",
        best_card_multiplier=Decimal("4"),
        wrong_card_count_this_week=RULE_4_MIN_WRONG_CARD_COUNT - 1,
    )
    assert _pick_rule(sig) is None


def test_rule_4_suppressed_by_14_day_global_window():
    """Rule 4 is rate-limited across all categories every 14 days."""
    sig = _signals(
        this_card_name="Chase Freedom",
        this_card_multiplier=Decimal("1"),
        best_card_name="Amex Gold",
        best_card_multiplier=Decimal("4"),
        wrong_card_count_this_week=RULE_4_MIN_WRONG_CARD_COUNT,
        last_card_mismatch_at="2026-05-10T00:00:00Z",
    )
    assert _pick_rule(sig) is None


def test_rule_4_skipped_when_no_better_card():
    """If this card already has the best multiplier, no mismatch to flag."""
    sig = _signals(
        this_card_name="Amex Gold",
        this_card_multiplier=Decimal("4"),
        best_card_name="Amex Gold",
        best_card_multiplier=Decimal("4"),
        wrong_card_count_this_week=RULE_4_MIN_WRONG_CARD_COUNT,
    )
    assert _pick_rule(sig) is None


# ---------------------------------------------------------------------------
# Priority order — multiple rules eligible
# ---------------------------------------------------------------------------


def test_priority_order_rule_1_wins_over_rule_2():
    """When both rules 1 and 2 are eligible, rule 1 fires (useful-once first)."""
    sig = _signals(
        # Rule 1 eligible.
        amount=Decimal("80"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT,
        prior_max_in_category_this_month=Decimal("50"),
        # Rule 2 also eligible.
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        this_week_count=4,
        prior_4w_avg_weekly_count=Decimal("2"),
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_SINGLE_TX_NOTABLE


def test_priority_order_rule_2_wins_over_rule_3():
    """Rule 2 (frequency) outranks rule 3 (cumulative delta) when both apply."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        this_week_count=4,
        prior_4w_avg_weekly_count=Decimal("2"),
        mtd_category_spend=Decimal("200"),
        monthly_baseline_category=Decimal("150"),
        days_remaining_in_month=12,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_WEEKLY_FREQUENCY


def test_priority_order_rule_3_wins_over_rule_4():
    """Rule 3 (delta) outranks rule 4 (card mismatch) when both apply."""
    sig = _signals(
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("200"),
        monthly_baseline_category=Decimal("150"),
        days_remaining_in_month=12,
        this_card_name="Chase Freedom",
        this_card_multiplier=Decimal("1"),
        best_card_name="Amex Gold",
        best_card_multiplier=Decimal("4"),
        wrong_card_count_this_week=3,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA


# ---------------------------------------------------------------------------
# Sentence formatting helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (101, "101st"),
        (111, "111th"),
    ],
)
def test_ordinal_handles_teens_and_standard_suffixes(n, expected):
    """Verify English ordinal suffix rules including the irregular teens."""
    assert _ordinal(n) == expected


def test_format_multiplier_strips_trailing_zeros():
    """4.00 renders as '4'; 1.50 renders as '1.5'."""
    assert _format_multiplier(Decimal("4")) == "4"
    assert _format_multiplier(Decimal("4.00")) == "4"
    assert _format_multiplier(Decimal("1.5")) == "1.5"
    assert _format_multiplier(Decimal("2.50")) == "2.5"


# ---------------------------------------------------------------------------
# Noise threshold (percent + absolute floor)
# ---------------------------------------------------------------------------


def test_noise_threshold_requires_both_percent_and_absolute():
    """Both the percent floor AND the $10 floor must pass for a delta to register."""
    # Passes percent (12% > 10%) but fails absolute ($6 < $10) — noise.
    assert _passes_noise_threshold(Decimal("6"), Decimal("50")) is False
    # Passes absolute ($20 > $10) but fails percent (5% < 10%) — noise.
    assert _passes_noise_threshold(Decimal("20"), Decimal("400")) is False
    # Passes both — signal.
    assert _passes_noise_threshold(Decimal("20"), Decimal("100")) is True


def test_noise_threshold_zero_baseline_is_noise():
    """A zero baseline always reads as noise — division would be undefined."""
    assert _passes_noise_threshold(Decimal("50"), Decimal("0")) is False


# Reference NOISE_PERCENT_FLOOR so the import is exercised; the value is
# also encoded in the test cases above (10% threshold).
def test_noise_percent_floor_is_ten_percent():
    """Document the percent-floor default used by the noise threshold tests."""
    assert NOISE_PERCENT_FLOOR == Decimal("0.10")


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _signals(**overrides: Any) -> _Signals:
    """Build a `_Signals` with sane defaults, override only what each test cares about."""
    defaults: dict[str, Any] = {
        "user_id": uuid4(),
        "category": "Dining",
        "amount": Decimal("10"),
        "card_id": None,
        "category_tx_count_prior": 0,
        "category_history_days": 0,
        "month_tx_count_in_category": 0,
        "prior_max_in_category_this_month": Decimal("0"),
        "this_week_count": 0,
        "prior_4w_avg_weekly_count": Decimal("0"),
        "mtd_category_spend": Decimal("0"),
        "monthly_baseline_category": Decimal("0"),
        "days_remaining_in_month": 15,
        "this_card_name": None,
        "this_card_multiplier": Decimal("1"),
        "best_card_name": None,
        "best_card_multiplier": Decimal("0"),
        "wrong_card_count_this_week": 0,
        "last_single_tx_notable_at": None,
        "last_weekly_frequency_at": None,
        "last_cumulative_delta_at": None,
        "last_card_mismatch_at": None,
    }
    defaults.update(overrides)
    return _Signals(**defaults)
