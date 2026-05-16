"""Dashboard request/response models — Day 13 (DESIGN.md §6.3).

Wire shape for `GET /dashboard/summary`. The dashboard fits one screen at
375×667 (CLAUDE.md invariant 9), so the response is deliberately small —
4–5 category tiles + headline + a single observation sentence.

`baseline_ready` exists at both the top level and per-category. The
top-level flag drives the empty-state vs populated branch in `home.tsx`;
the per-category flag drives the "still learning" badge on individual
tiles. They flip independently so a user can have a confident dining
baseline while groceries is still learning.

Money is `Decimal` to match `transactions.amount` (`numeric` in
Postgres). Floats do not belong on this path (invariant 13).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

TileColor = Literal["green", "neutral", "amber", "red"]


class CategoryTile(BaseModel):
    """One row in `categories[]` — one dashboard tile.

    Fields:
        name: Category name from the closed enum (DESIGN.md §6.1).
        this_month: Sum of the user's spend in this category for the
            current month-to-date, in home currency.
        baseline: Trailing 3-month average per-month spend in this
            category. `None` when `baseline_ready` is false.
        delta_abs: this_month - baseline. `None` when not ready.
        delta_pct: 100 * delta_abs / baseline. `None` when not ready or
            baseline is zero.
        color: Visual treatment hint for the tile. Always present; when
            not ready, defaults to "neutral".
        baseline_ready: True iff the category has ≥6 prior transactions
            AND ≥30 days of history before the current month. False →
            the frontend renders the "still learning" badge in place of
            the delta.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    this_month: Decimal
    baseline: Decimal | None
    delta_abs: Decimal | None
    delta_pct: float | None
    color: TileColor
    baseline_ready: bool


class DashboardSummary(BaseModel):
    """`GET /dashboard/summary` response.

    Fields:
        this_month: Total spend across all categories, month-to-date.
        baseline: Trailing 3-month average total monthly spend across
            all categories. `None` when no category has cleared the
            soft new-user gate.
        delta_pct: 100 * (this_month - baseline) / baseline. `None`
            when baseline is unavailable or zero.
        baseline_ready: True iff at least one category has cleared the
            soft gate. The top-level headline delta is only meaningful
            once this flips; until then the dashboard shows the empty
            or "still learning" experience.
        observation: One-sentence prose summary the dashboard renders
            below the headline. `None` for the truly-zero-history user
            (the empty-state copy lives in the frontend in that case).
        categories: Up to 4–5 tiles, sorted by descending absolute
            delta magnitude. Categories with zero this-month spend and
            no historical baseline are dropped from the list.
    """

    model_config = ConfigDict(extra="forbid")

    this_month: Decimal
    baseline: Decimal | None
    delta_pct: float | None
    baseline_ready: bool
    observation: str | None
    categories: list[CategoryTile]
