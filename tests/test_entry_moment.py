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

from datetime import date
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
    RULE_3_MIN_DAYS_FOR_PROJECTION,
    RULE_4_MIN_WRONG_CARD_COUNT,
    RULE_5_MIN_PRIOR_MONTH_COUNT,
    RULE_6_MIN_WEEK_COUNT,
    RULE_CARD_MISMATCH,
    RULE_CATEGORY_SHARE,
    RULE_CUMULATIVE_DELTA,
    RULE_LARGEST_THIS_WEEK,
    RULE_PACING_UNDER,
    RULE_SINGLE_TX_NOTABLE,
    RULE_WEEKLY_FREQUENCY,
    SEVERITY_ALERT,
    SEVERITY_CALM,
    SEVERITY_ELEVATED,
    SEVERITY_POSITIVE,
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
    assert decision.severity == SEVERITY_CALM


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
    assert decision.severity == SEVERITY_CALM


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
# _pick_rule — rule 3 (cumulative delta, pace-aware)
# ---------------------------------------------------------------------------


def test_rule_3_forecast_fires_elevated_when_pace_moderately_over():
    """Rule 3 forecast framing fires `elevated` at a 10-25% projected overage."""
    # Day 20 of a 30-day month, $160 MTD → projected $240, $40 (20%) over
    # the $200 baseline → elevated (amber) band.
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("160"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA
    assert decision.severity == SEVERITY_ELEVATED
    assert decision.sentence == "on pace for about $40 over your monthly dining average."


def test_rule_3_forecast_fires_alert_when_pace_far_over():
    """Rule 3 forecast framing fires `alert` once the projected overage tops 25%."""
    # Day 20 of a 30-day month, $240 MTD → projected $360, $160 (80%) over
    # the $200 baseline → alert (terracotta) band.
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("240"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA
    assert decision.severity == SEVERITY_ALERT
    assert (
        decision.sentence
        == "on pace for about $160 over your monthly dining average."
    )


def test_rule_3_retrospective_framing_in_first_days_of_month():
    """Before RULE_3_MIN_DAYS_FOR_PROJECTION, rule 3 uses the retrospective sentence."""
    # Day 3 — too early to project from 2-3 days of data; compare MTD
    # directly. $300 vs $200 → $100 (50%) over → alert, retrospective
    # phrasing that still names the days left.
    sig = _signals(
        txn_date=date(2026, 6, 3),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("300"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=27,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA
    assert decision.severity == SEVERITY_ALERT
    assert "above your monthly dining average" in decision.sentence
    assert "27 days left" in decision.sentence


def test_rule_3_min_days_constant_is_the_forecast_cutoff():
    """Document the day-of-month boundary between retrospective and forecast framing."""
    assert RULE_3_MIN_DAYS_FOR_PROJECTION == 5


def test_rule_3_suppressed_when_rule_2_already_fired_this_week():
    """Rule 3 backs off when rule 2 already fired in the same category this week."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        # Rule 2's signals do NOT meet — to isolate the suppression check.
        this_week_count=0,
        prior_4w_avg_weekly_count=Decimal("0"),
        mtd_category_spend=Decimal("240"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
        last_weekly_frequency_at="2026-06-12T00:00:00Z",
    )
    assert _pick_rule(sig) is None


def test_rule_3_forecast_skipped_when_projected_delta_below_absolute_floor():
    """A projected delta under the $10 absolute floor is treated as noise."""
    # Day 20, $135 MTD → projected $202.50, $2.50 over $200 — below the floor.
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("135"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    assert _pick_rule(sig) is None


def test_rule_3_forecast_skipped_when_projected_delta_below_percent_floor():
    """A projected delta over $10 but under 10% of a large baseline is noise."""
    # Day 20, $1400 MTD → projected $2100, $100 over $2000 = 5% — below 10%.
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("1400"),
        monthly_baseline_category=Decimal("2000"),
        days_remaining_in_month=10,
    )
    assert _pick_rule(sig) is None


def test_rule_3_skipped_on_last_day_of_month():
    """Rule 3's phrasing assumes some month is left — last day is a no-op."""
    sig = _signals(
        txn_date=date(2026, 6, 30),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("300"),
        monthly_baseline_category=Decimal("200"),
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
    assert decision.severity == SEVERITY_CALM


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


def test_rule_4_suppressed_by_recent_fire_in_category():
    """Rule 4 is rate-limited once per category every 14 days.

    `last_card_mismatch_at` is now populated by the RPC from a
    *per-category* window (was global before 2026-07-03); at the unit level
    a non-null value still hard-suppresses. The per-category scoping itself
    lives in `entry_moment_signals` and is covered by the SQL integration
    suite.
    """
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
# _pick_rule — rule 5 (category share, gate-free warm-up)
# ---------------------------------------------------------------------------


def test_rule_5_category_share_fires_without_soft_gate():
    """Rule 5 fires in a user's first month — it needs no baseline history."""
    sig = _signals(
        amount=Decimal("40"),
        month_tx_count_in_category=RULE_5_MIN_PRIOR_MONTH_COUNT,
        mtd_category_spend=Decimal("100"),
        # Soft gate deliberately NOT met — proving rule 5 is gate-free.
        category_tx_count_prior=0,
        category_history_days=0,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CATEGORY_SHARE
    assert decision.sentence == "that's 40% of your dining spending this month."
    assert decision.severity == SEVERITY_CALM


def test_rule_5_skipped_below_share_floor():
    """A purchase under RULE_5_MIN_SHARE of the month's category is not notable."""
    sig = _signals(
        amount=Decimal("30"),
        month_tx_count_in_category=RULE_5_MIN_PRIOR_MONTH_COUNT,
        mtd_category_spend=Decimal("100"),  # 30% < 35% floor.
    )
    assert _pick_rule(sig) is None


def test_rule_5_skipped_under_min_prior_month_count():
    """Rule 5 needs a few prior in-month tx so 'X% this month' ranks against more than one."""
    sig = _signals(
        amount=Decimal("40"),
        month_tx_count_in_category=RULE_5_MIN_PRIOR_MONTH_COUNT - 1,
        mtd_category_spend=Decimal("100"),
    )
    assert _pick_rule(sig) is None


def test_rule_5_skipped_when_share_exceeds_one_refund_distortion():
    """A refund-distorted month (share > 1) is skipped rather than shown as >100%."""
    sig = _signals(
        amount=Decimal("60"),
        month_tx_count_in_category=RULE_5_MIN_PRIOR_MONTH_COUNT,
        mtd_category_spend=Decimal("50"),  # amount > mtd ⇒ share 1.2.
    )
    assert _pick_rule(sig) is None


def test_rule_5_suppressed_by_recent_fire():
    """Rule 5 honors the once-per-category-per-7-days rate limit."""
    sig = _signals(
        amount=Decimal("40"),
        month_tx_count_in_category=RULE_5_MIN_PRIOR_MONTH_COUNT,
        mtd_category_spend=Decimal("100"),
        last_category_share_at="2026-05-18T00:00:00Z",
    )
    assert _pick_rule(sig) is None


# ---------------------------------------------------------------------------
# _pick_rule — rule 6 (largest this week, gate-free warm-up)
# ---------------------------------------------------------------------------


def test_rule_6_largest_this_week_fires_without_soft_gate():
    """Rule 6 fires when this is strictly the biggest purchase this week."""
    sig = _signals(
        amount=Decimal("100"),
        week_tx_count_all=RULE_6_MIN_WEEK_COUNT,
        week_prior_max_all=Decimal("50"),
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_LARGEST_THIS_WEEK
    assert decision.sentence == "biggest single purchase this week."
    assert decision.severity == SEVERITY_CALM


def test_rule_6_skipped_when_not_strictly_largest():
    """Rule 6 requires strict greater-than — a tie with the week's prior max is silent."""
    sig = _signals(
        amount=Decimal("50"),
        week_tx_count_all=RULE_6_MIN_WEEK_COUNT,
        week_prior_max_all=Decimal("50"),
    )
    assert _pick_rule(sig) is None


def test_rule_6_skipped_under_min_week_count():
    """With too few purchases this week, 'biggest this week' is trivially true — skip it."""
    sig = _signals(
        amount=Decimal("100"),
        week_tx_count_all=RULE_6_MIN_WEEK_COUNT - 1,
        week_prior_max_all=Decimal("50"),
    )
    assert _pick_rule(sig) is None


def test_rule_6_suppressed_by_recent_fire():
    """Rule 6 honors the once-per-7-days (global) rate limit."""
    sig = _signals(
        amount=Decimal("100"),
        week_tx_count_all=RULE_6_MIN_WEEK_COUNT,
        week_prior_max_all=Decimal("50"),
        last_largest_this_week_at="2026-05-18T00:00:00Z",
    )
    assert _pick_rule(sig) is None


# ---------------------------------------------------------------------------
# _pick_rule — rule 7 (pacing under, positive)
# ---------------------------------------------------------------------------


def test_rule_7_pacing_under_fires_positive():
    """Rule 7 fires green when projected spend is comfortably under baseline."""
    # Day 20 of a 30-day month, $100 MTD → projected $150, $50 (25%) under
    # the $200 baseline → positive (moss) band.
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("100"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_PACING_UNDER
    assert decision.severity == SEVERITY_POSITIVE
    assert decision.sentence == "on pace for about $50 under your monthly dining average."


def test_rule_7_requires_soft_gate():
    """Rule 7 asserts a personal baseline, so it needs the soft gate like rule 3."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE - 1,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("100"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    assert _pick_rule(sig) is None


def test_rule_7_skipped_below_under_ratio():
    """A projection only slightly under baseline (<15%) is not a 'you're okay' moment."""
    # Day 20, $120 MTD → projected $180, $20 (10%) under $200 — under the
    # 15% positive floor.
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("120"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    assert _pick_rule(sig) is None


def test_rule_7_skipped_in_first_days_of_month():
    """Forecast-only: a 'you're under' claim before day 5 is meaningless."""
    sig = _signals(
        txn_date=date(2026, 6, 3),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("5"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=27,
    )
    assert _pick_rule(sig) is None


def test_rule_7_suppressed_by_recent_fire():
    """Rule 7 honors the once-per-category-per-14-days rate limit."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("100"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
        last_pacing_under_at="2026-06-10T00:00:00Z",
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
    assert decision.severity == SEVERITY_CALM


def test_priority_rule_2_wins_over_elevated_rule_3():
    """An `elevated`-tier rule 3 keeps slot 3 — rule 2 (frequency) still outranks it."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        # Rule 2 eligible.
        this_week_count=4,
        prior_4w_avg_weekly_count=Decimal("2"),
        # Rule 3 eligible but only `elevated` (20% projected overage).
        mtd_category_spend=Decimal("160"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_WEEKLY_FREQUENCY


def test_priority_order_rule_3_wins_over_rule_4():
    """Rule 3 (delta) outranks rule 4 (card mismatch) when both apply."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("160"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
        this_card_name="Chase Freedom",
        this_card_multiplier=Decimal("1"),
        best_card_name="Amex Gold",
        best_card_multiplier=Decimal("4"),
        wrong_card_count_this_week=3,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA


def test_priority_alert_rule_3_outranks_rule_1():
    """An `alert`-tier rule 3 jumps the queue — it outranks even rule 1."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        # Rule 1 eligible.
        amount=Decimal("80"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT,
        prior_max_in_category_this_month=Decimal("50"),
        # Rule 3 eligible and `alert` (80% projected overage).
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        mtd_category_spend=Decimal("240"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA
    assert decision.severity == SEVERITY_ALERT


def test_priority_alert_rule_3_outranks_rule_2():
    """An `alert`-tier rule 3 also outranks rule 2 (elevated weekly frequency)."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        # Rule 2 eligible.
        this_week_count=4,
        prior_4w_avg_weekly_count=Decimal("2"),
        # Rule 3 eligible and `alert`.
        mtd_category_spend=Decimal("240"),
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CUMULATIVE_DELTA
    assert decision.severity == SEVERITY_ALERT


def test_priority_real_signal_outranks_warmup_rules():
    """A real signal (rule 1) beats the gate-free warm-up rules when both apply."""
    sig = _signals(
        # Rule 1 eligible.
        amount=Decimal("80"),
        month_tx_count_in_category=RULE_1_MIN_MONTH_COUNT,
        prior_max_in_category_this_month=Decimal("50"),
        # Rule 5 (category_share) also eligible: $80 of $100 MTD = 80%.
        mtd_category_spend=Decimal("100"),
        # Rule 6 (largest_this_week) also eligible.
        week_tx_count_all=RULE_6_MIN_WEEK_COUNT,
        week_prior_max_all=Decimal("50"),
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_SINGLE_TX_NOTABLE


def test_priority_warmup_outranks_positive_pacing_under():
    """The warm-up rules sit above the positive rule; pacing_under is last resort."""
    sig = _signals(
        txn_date=date(2026, 6, 20),
        category_tx_count_prior=MIN_TX_COUNT_FOR_BASELINE,
        category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        # Rule 5 (category_share) eligible: $40 of $100 MTD = 40%.
        amount=Decimal("40"),
        month_tx_count_in_category=RULE_5_MIN_PRIOR_MONTH_COUNT,
        mtd_category_spend=Decimal("100"),
        # Rule 7 (pacing_under) also eligible: projected $150 vs $200 (25% under).
        monthly_baseline_category=Decimal("200"),
        days_remaining_in_month=10,
    )
    decision = _pick_rule(sig)
    assert decision is not None
    assert decision.rule_id == RULE_CATEGORY_SHARE


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
        "txn_date": date(2026, 5, 20),
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
        "week_tx_count_all": 0,
        "week_prior_max_all": Decimal("0"),
        "last_category_share_at": None,
        "last_largest_this_week_at": None,
        "last_pacing_under_at": None,
    }
    defaults.update(overrides)
    return _Signals(**defaults)
