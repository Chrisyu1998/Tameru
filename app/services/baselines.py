"""Dashboard baseline + tile computation — Day 13 (DESIGN.md §6.3).

`compute_dashboard_summary(user)` is the single function `GET /dashboard/summary`
calls. It invokes the `dashboard_summary(p_today)` Postgres RPC for one
round-trip of aggregated data, applies the soft new-user gate per category,
picks the observation sentence, color-codes the tiles, and returns a
typed `DashboardSummary`.

Why the gate is here rather than in SQL: the gate's "≥6 prior transactions
AND ≥30 days of history" threshold is a product decision that may flex
(Day 13 prompt). Keeping it in Python keeps SQL migrations stable across
threshold tweaks, and the per-category counts/days the SQL function
returns are reusable for future surfaces (weekly digest, etc.).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.util.timezone import user_local_today
from app.models.dashboard import CategoryTile, DashboardSummary, TileColor

MIN_TX_COUNT_FOR_BASELINE = 6
MIN_HISTORY_DAYS_FOR_BASELINE = 30
MAX_TILES = 5

EMPTY_HISTORY_OBSERVATION = (
    "keep logging — your patterns will surface here as you build history."
)


def compute_dashboard_summary(
    user: AuthedUser, today: date | None = None
) -> DashboardSummary:
    """Build the dashboard payload for one user.

    Request: caller's `AuthedUser` (JWT-scoped) plus an optional `today`
    override for tests. Production passes `None` and `p_today` resolves
    to the user's local date via `users_meta.timezone` (UTC fallback) —
    a server-UTC anchor would drop east-of-UTC users' local-today rows
    from "this month" every morning, and show the previous month as
    "this month" for the first local hours of the 1st (audit P2-7).

    Response: `DashboardSummary` — top-level `this_month`/`baseline`/
    `delta_pct`, `baseline_ready`, an `observation` sentence (or null),
    and up to `MAX_TILES` category tiles sorted by descending delta
    magnitude. Categories with no historical spend AND no current spend
    are omitted; the truly-empty user gets `categories=[]` and
    `baseline_ready=false`.
    """
    today = today or user_local_today(user.jwt)
    client = supabase_for_user(user.jwt)
    resp = client.rpc(
        "dashboard_summary", {"p_today": today.isoformat()}
    ).execute()
    rows: list[dict[str, Any]] = resp.data or []

    tiles_all = [_tile_from_row(row) for row in rows]

    # Drop rows with neither current spend nor historical baseline — they
    # represent categories the user once tagged in a renamed merchant but
    # have no signal to show.
    tiles_meaningful = [
        t for t in tiles_all if t.this_month != 0 or (t.baseline or 0) != 0
    ]

    # Sort: baseline-ready tiles with the largest |delta_abs| first; then
    # not-ready tiles by this_month spend. The dashboard surfaces movement,
    # not bulk — a steady category does not earn a slot when something is
    # actually moving (DESIGN.md §6.2).
    tiles_sorted = sorted(
        tiles_meaningful,
        key=lambda t: (
            0 if t.baseline_ready else 1,
            -abs(t.delta_abs or Decimal(0)),
            -t.this_month,
        ),
    )
    tiles_visible = tiles_sorted[:MAX_TILES]

    top_this_month = sum((t.this_month for t in tiles_meaningful), Decimal(0))
    top_baseline = _top_baseline(tiles_meaningful)
    top_delta_pct = _delta_pct(top_this_month, top_baseline)
    top_baseline_ready = any(t.baseline_ready for t in tiles_meaningful)

    return DashboardSummary(
        this_month=top_this_month,
        baseline=top_baseline,
        delta_pct=top_delta_pct,
        baseline_ready=top_baseline_ready,
        observation=_observation(top_baseline_ready, tiles_visible, top_this_month, top_baseline),
        categories=tiles_visible,
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _tile_from_row(row: dict[str, Any]) -> CategoryTile:
    """Build one `CategoryTile` from a `dashboard_summary` RPC row.

    Applies the soft new-user gate per category and computes delta /
    color when ready. Numbers come back as JSON strings (for `numeric`)
    or ints — coerce explicitly so the Pydantic Decimal validators
    don't reject them.
    """
    this_month = Decimal(str(row.get("this_month") or 0))
    baseline_raw = Decimal(str(row.get("monthly_baseline") or 0))
    tx_count = int(row.get("category_tx_count") or 0)
    history_days = int(row.get("category_history_days") or 0)

    ready = (
        tx_count >= MIN_TX_COUNT_FOR_BASELINE
        and history_days >= MIN_HISTORY_DAYS_FOR_BASELINE
        and baseline_raw > 0
    )
    baseline = baseline_raw if ready else None
    delta_abs = (this_month - baseline_raw) if ready else None
    delta_pct = _delta_pct(this_month, baseline_raw) if ready else None
    color = _color(delta_pct) if ready else "neutral"

    return CategoryTile(
        name=row["category"],
        this_month=this_month,
        baseline=baseline,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        color=color,
        baseline_ready=ready,
    )


def _top_baseline(tiles: list[CategoryTile]) -> Decimal | None:
    """Sum of per-category baselines across tiles that have one.

    Returns `None` if no category has cleared the gate — the headline
    delta is only meaningful once at least one baseline exists.
    """
    ready_baselines = [t.baseline for t in tiles if t.baseline is not None]
    if not ready_baselines:
        return None
    return sum(ready_baselines, Decimal(0))


def _delta_pct(this_month: Decimal, baseline: Decimal | None) -> float | None:
    """Compute 100 * (this_month - baseline) / baseline.

    Returns `None` when baseline is missing or zero — division-by-zero
    is undefined and surfacing infinity would break the tile color
    bucketing downstream.
    """
    if baseline is None or baseline == 0:
        return None
    return float((this_month - baseline) / baseline) * 100.0


def _color(delta_pct: float | None) -> TileColor:
    """Bucket a delta percentage into one of the four tile colors.

    Buckets (matches the existing `tone` palette in DeltaTile.tsx):
      - delta_pct < -10%   → green  (genuinely under-spending)
      - -10% to +10%       → neutral (within noise)
      - +10% to +30%       → amber  (mildly above)
      - > +30%             → red    (notably above)

    `None` defaults to neutral.
    """
    if delta_pct is None:
        return "neutral"
    if delta_pct < -10.0:
        return "green"
    if delta_pct <= 10.0:
        return "neutral"
    if delta_pct <= 30.0:
        return "amber"
    return "red"


def _observation(
    baseline_ready: bool,
    tiles: list[CategoryTile],
    this_month: Decimal,
    baseline: Decimal | None,
) -> str | None:
    """Pick the one-sentence dashboard observation.

    Mirrors the prior frontend `buildObservation` so the swap is
    invisible to the user. Returns `None` for the empty-history user
    when even the "keep logging" string would be redundant with the
    frontend's empty-state copy — actually we surface the keep-logging
    string here so the frontend layer has no prose-generation logic.
    """
    if not baseline_ready:
        return EMPTY_HISTORY_OBSERVATION if not tiles else None
    if not tiles:
        return "a quiet start to the month."

    above = [t for t in tiles if (t.delta_abs or Decimal(0)) > 0]
    below = [t for t in tiles if (t.delta_abs or Decimal(0)) < 0]

    if above and baseline is not None and this_month > baseline:
        top = above[0]
        return f"{top.name.lower()} is doing most of the lifting this month."
    if len(below) > len(above):
        return "you're spending more deliberately than usual."
    return "things are roughly where they always sit."
