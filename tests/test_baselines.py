"""Unit tests for the dashboard baselines service — Day 13.

`_tile_from_row`, the soft new-user gate, color bucketing, and observation
selection are pure functions over the `dashboard_summary` RPC's row shape.
These tests synthesize rows directly and verify the Python behavior atop
them. The SQL function (3-month rolling avg, history-days, etc.) is
exercised end-to-end by `tests/routes/test_dashboard.py`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.models.dashboard import CategoryTile, DashboardSummary
from app.services.baselines import (
    EMPTY_HISTORY_OBSERVATION,
    MIN_HISTORY_DAYS_FOR_BASELINE,
    MIN_TX_COUNT_FOR_BASELINE,
    _color,
    _delta_pct,
    _observation,
    _tile_from_row,
    _top_baseline,
)


# ---------------------------------------------------------------------------
# Tile construction from RPC rows
# ---------------------------------------------------------------------------


def test_tile_marks_baseline_ready_only_when_both_gates_clear():
    """Soft gate fires only when tx count ≥ 6 AND history ≥ 30 days AND baseline > 0."""
    ready = _tile_from_row(
        _row(
            category="Dining",
            this_month="120",
            monthly_baseline="100",
            category_tx_count=MIN_TX_COUNT_FOR_BASELINE,
            category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        )
    )
    assert ready.baseline_ready is True
    assert ready.baseline == Decimal("100")
    assert ready.delta_abs == Decimal("20")
    assert ready.delta_pct == 20.0
    assert ready.color == "amber"


def test_tile_not_ready_when_one_gate_misses():
    """Under either gate threshold, baseline / delta_abs / delta_pct are null."""
    under_count = _tile_from_row(
        _row(
            category="Dining",
            this_month="120",
            monthly_baseline="100",
            category_tx_count=MIN_TX_COUNT_FOR_BASELINE - 1,
            category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        )
    )
    assert under_count.baseline_ready is False
    assert under_count.baseline is None
    assert under_count.delta_abs is None
    assert under_count.delta_pct is None
    assert under_count.color == "neutral"

    under_days = _tile_from_row(
        _row(
            category="Dining",
            this_month="120",
            monthly_baseline="100",
            category_tx_count=MIN_TX_COUNT_FOR_BASELINE,
            category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE - 1,
        )
    )
    assert under_days.baseline_ready is False


def test_tile_not_ready_when_baseline_is_zero():
    """A category with prior tx but $0 baseline cannot anchor a real delta."""
    tile = _tile_from_row(
        _row(
            category="Dining",
            this_month="120",
            monthly_baseline="0",
            category_tx_count=MIN_TX_COUNT_FOR_BASELINE,
            category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        )
    )
    assert tile.baseline_ready is False


def test_tile_historic_only_renders_full_negative_delta():
    """Category spent historically but not this month → delta = -baseline."""
    tile = _tile_from_row(
        _row(
            category="Dining",
            this_month="0",
            monthly_baseline="80",
            category_tx_count=MIN_TX_COUNT_FOR_BASELINE,
            category_history_days=MIN_HISTORY_DAYS_FOR_BASELINE,
        )
    )
    assert tile.baseline_ready is True
    assert tile.this_month == Decimal("0")
    assert tile.delta_abs == Decimal("-80")
    assert tile.delta_pct == -100.0
    assert tile.color == "green"


# ---------------------------------------------------------------------------
# Color bucketing
# ---------------------------------------------------------------------------


def test_color_buckets_match_spec():
    """Verify the percent→color bucket boundaries.

    Buckets:
      delta < -10%       → green
      -10% ≤ d ≤ +10%    → neutral (within-noise band)
      +10% < d ≤ +30%    → amber
      d > +30%           → red
    """
    assert _color(-25.0) == "green"
    assert _color(-10.0) == "neutral"
    assert _color(0.0) == "neutral"
    assert _color(10.0) == "neutral"
    assert _color(10.1) == "amber"
    assert _color(30.0) == "amber"
    assert _color(30.1) == "red"
    assert _color(None) == "neutral"


# ---------------------------------------------------------------------------
# delta_pct and top_baseline
# ---------------------------------------------------------------------------


def test_delta_pct_returns_none_for_zero_or_missing_baseline():
    """A zero/missing baseline yields `None` rather than division-by-zero."""
    assert _delta_pct(Decimal("100"), Decimal("0")) is None
    assert _delta_pct(Decimal("100"), None) is None


def test_top_baseline_sums_ready_tiles_only():
    """The top-level headline baseline is the sum of per-tile baselines that exist."""
    tiles = [
        CategoryTile(
            name="Dining",
            this_month=Decimal("120"),
            baseline=Decimal("100"),
            delta_abs=Decimal("20"),
            delta_pct=20.0,
            color="amber",
            baseline_ready=True,
        ),
        CategoryTile(
            name="Groceries",
            this_month=Decimal("50"),
            baseline=Decimal("60"),
            delta_abs=Decimal("-10"),
            delta_pct=-16.67,
            color="green",
            baseline_ready=True,
        ),
        CategoryTile(
            name="Coffee Shops",
            this_month=Decimal("15"),
            baseline=None,
            delta_abs=None,
            delta_pct=None,
            color="neutral",
            baseline_ready=False,
        ),
    ]
    assert _top_baseline(tiles) == Decimal("160")


def test_top_baseline_none_when_no_tile_ready():
    """Without any baseline-ready tile, the headline baseline is None."""
    tile = CategoryTile(
        name="Coffee Shops",
        this_month=Decimal("15"),
        baseline=None,
        delta_abs=None,
        delta_pct=None,
        color="neutral",
        baseline_ready=False,
    )
    assert _top_baseline([tile]) is None


# ---------------------------------------------------------------------------
# Observation copy
# ---------------------------------------------------------------------------


def test_observation_keep_logging_for_empty_history():
    """Truly-empty user (no tiles, no baseline_ready) sees the keep-logging prompt."""
    assert _observation(False, [], Decimal("0"), None) == EMPTY_HISTORY_OBSERVATION


def test_observation_top_lifter_when_any_above_and_total_exceeds_baseline():
    """When this_month > baseline AND at least one tile is above, name the top lifter."""
    tiles = [_ready_tile("Dining", delta=Decimal("30"))]
    text = _observation(True, tiles, Decimal("130"), Decimal("100"))
    assert text == "dining is doing most of the lifting this month."


def test_observation_deliberate_when_more_below_than_above():
    """More tiles below baseline than above → reflective observation."""
    tiles = [
        _ready_tile("Dining", delta=Decimal("-10")),
        _ready_tile("Groceries", delta=Decimal("-15")),
        _ready_tile("Coffee Shops", delta=Decimal("5")),
    ]
    text = _observation(True, tiles, Decimal("90"), Decimal("110"))
    assert text == "you're spending more deliberately than usual."


def test_observation_steady_fallback():
    """When neither over-spending nor under-spending dominates, surface steady."""
    tiles = [
        _ready_tile("Dining", delta=Decimal("5")),
        _ready_tile("Groceries", delta=Decimal("3")),
    ]
    text = _observation(True, tiles, Decimal("105"), Decimal("100"))
    # this_month > baseline → lifter sentence wins; this regression test
    # forces the steady branch by pushing this_month BELOW baseline.
    text = _observation(True, tiles, Decimal("99"), Decimal("100"))
    assert text == "things are roughly where they always sit."


# Reference DashboardSummary so the import is exercised when the test file
# is imported by collect-only runs that want to assert the wire shape stays
# in sync with this service module.
def test_dashboard_summary_wire_shape_imports():
    """Document that DashboardSummary remains importable from this test module."""
    assert DashboardSummary.__name__ == "DashboardSummary"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _row(**fields: Any) -> dict[str, Any]:
    """Build a synthetic `dashboard_summary` RPC row.

    Defaults cover the columns the service reads; tests override the
    subset they care about. Numerics arrive as strings from Supabase RPC
    so we keep that wire shape.
    """
    return {
        "category": "Dining",
        "this_month": "0",
        "monthly_baseline": "0",
        "category_tx_count": 0,
        "category_history_days": 0,
        **fields,
    }


def _ready_tile(name: str, delta: Decimal) -> CategoryTile:
    """Synthesize a baseline-ready tile with a given delta for observation tests."""
    this_month = Decimal("100") + delta
    return CategoryTile(
        name=name,
        this_month=this_month,
        baseline=Decimal("100"),
        delta_abs=delta,
        delta_pct=float(delta),
        color="neutral",
        baseline_ready=True,
    )
