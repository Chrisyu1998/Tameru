"""Goals service — list with period-to-date spend, in-place PATCH, hard DELETE.

The list path computes per-goal spend by summing the user's
`active_transactions` over the goal's calendar-aligned window:

  * `week`  → Monday..Sunday containing `today`
  * `month` → 1..last-of-month containing `today`
  * `year`  → Jan 1..Dec 31 of `today`

PostgREST has no native SUM, so we page through the matching rows and
sum in Python. Unlike `calculate_total` (an agent tool that surfaces
truncation via a `truncated: bool` flag in the response so the model
can phrase its answer accordingly), the GET /goals endpoint drives a
visible progress bar — silent undercounting would make the bar lie.
A safety cap on total pages bounds the worst case if RLS ever fails
to narrow the read, so a pathological scenario surfaces as an
HTTPException rather than an OOM.

RLS enforces `user_id = auth.uid()`; the queries omit explicit user_id
filters deliberately (DESIGN.md §8.13).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from postgrest.exceptions import APIError

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.util.timezone import user_local_today
from app.models.goals import (
    Goal,
    GoalPatchRequest,
    GoalsListResponse,
    GoalWithSpend,
)

# Page size for the spend read — small enough that one query is cheap,
# large enough that month-windows of typical density (a few dozen txs)
# resolve in a single round-trip.
_SPEND_PAGE_SIZE = 250

# Hard safety cap on pages per spend computation. v1's expected scale is
# tens of transactions per goal-window; this cap (250 * 80 = 20k rows)
# is two orders of magnitude above realistic and is purely a defensive
# bound — if a user somehow blows through it we 500 with a clear error
# rather than infinite-looping or hiding rows from the sum.
_SPEND_MAX_PAGES = 80


def list_goals_with_spend(
    user: AuthedUser, today: _dt.date | None = None
) -> GoalsListResponse:
    """Return all of the user's goals, each with period-to-date spend.

    Request:
        `today` defaults to the server's current date; tests inject a
        fixed date to make windowing deterministic.

    Response:
        `GoalsListResponse(items=[GoalWithSpend(...), ...])`. Overall
        budgets (`category is None`) sum across all categories. Goals are
        returned in (category NULLS FIRST, category ASC) order so the
        frontend can pin the overall-budget row to the top.
    """
    today = today or user_local_today(user.jwt)
    client = supabase_for_user(user.jwt)

    goals_resp = (
        client.table("goals")
        .select("id, user_id, category, amount, period, created_at, updated_at")
        .execute()
    )
    rows = goals_resp.data or []
    if not rows:
        return GoalsListResponse(items=[])

    items: list[GoalWithSpend] = []
    for row in rows:
        goal = Goal.model_validate(row)
        window_start, window_end = _window_for(goal.period, today)
        spent = _sum_active_transactions(
            client, goal.category, window_start, window_end
        )
        progress = float(spent / goal.amount) if goal.amount > 0 else 0.0
        items.append(
            GoalWithSpend(
                goal=goal,
                spent_period_to_date=spent,
                window_start=window_start,
                window_end=window_end,
                progress_ratio=progress,
            )
        )

    items.sort(key=lambda g: (g.goal.category is not None, g.goal.category or ""))
    return GoalsListResponse(items=items)


def update_goal(
    user: AuthedUser, goal_id: UUID, patch: GoalPatchRequest
) -> Goal:
    """Update amount and/or period on a goal owned by `user`.

    Request:
        `{amount?: Decimal, period?: "week"|"month"|"year"}` — at least
        one field set (enforced at the model layer).

    Response:
        The updated `Goal` row.

    Raises:
        404 if the goal doesn't exist or belongs to another user (RLS
        hides the distinction).
        409 with `{detail: {code: "goal_slot_occupied", ...}}` if the
        requested period would collide with an existing goal in the same
        `(user, category, period)` slot.
    """
    client = supabase_for_user(user.jwt)

    # Pre-flight check so RLS misses surface as 404 rather than a silent
    # zero-row update. Mirrors the pattern in routes/memory.py.
    current = (
        client.table("goals")
        .select("id, user_id, category, amount, period, created_at, updated_at")
        .eq("id", str(goal_id))
        .execute()
    )
    if not current.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    update: dict[str, object] = {}
    if patch.amount is not None:
        update["amount"] = str(patch.amount)
    if patch.period is not None:
        update["period"] = patch.period

    try:
        resp = (
            client.table("goals")
            .update(update)
            .eq("id", str(goal_id))
            .execute()
        )
    except APIError as err:
        # `23505` is Postgres' unique_violation — surfaces when the new
        # period collides with the user's existing (category, period)
        # slot. PostgREST wraps it as an APIError carrying the SQLSTATE.
        if _is_unique_violation(err):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "goal_slot_occupied",
                    "message": (
                        "you already have a goal for this category and period — "
                        "delete it first, or pick a different period."
                    ),
                },
            ) from err
        raise

    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Goal.model_validate(resp.data[0])


def delete_goal(user: AuthedUser, goal_id: UUID) -> None:
    """Hard-delete a goal owned by `user`.

    No FK from `transactions` to `goals`, so deletes are safe and durable.
    RLS makes "delete nonexistent" and "delete someone else's row"
    indistinguishable — both no-op and return successfully, matching the
    cards / memory DELETE semantics.
    """
    client = supabase_for_user(user.jwt)
    client.table("goals").delete().eq("id", str(goal_id)).execute()


# ---------------------------------------------------------------------------
# Private helpers.
# ---------------------------------------------------------------------------


def _window_for(period: str, today: _dt.date) -> tuple[_dt.date, _dt.date]:
    """Return the calendar-aligned [start, end] for the given period.

    `week` follows ISO (Monday start). `month` is the first/last of
    `today`'s month. `year` is Jan 1..Dec 31. The end-of-month math uses
    a next-month-minus-one-day trick that handles February correctly
    without a calendar library.
    """
    if period == "week":
        start = today - _dt.timedelta(days=today.weekday())
        end = start + _dt.timedelta(days=6)
        return start, end
    if period == "month":
        start = today.replace(day=1)
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - _dt.timedelta(days=1)
        return start, end
    if period == "year":
        return _dt.date(today.year, 1, 1), _dt.date(today.year, 12, 31)
    raise ValueError(f"unknown period {period!r}")


def _sum_active_transactions(
    client, category: str | None, window_start: _dt.date, window_end: _dt.date
) -> Decimal:
    """Sum `active_transactions.amount` within the window, optionally by category.

    `category is None` means an overall budget — sum every category.
    Reads through `active_transactions` so soft-deleted rows are
    excluded (DESIGN.md §8 status-column doctrine).

    Pages through results — a single goal's window can legitimately
    exceed any single fixed cap (especially overall yearly budgets),
    and silent truncation would make the progress bar lie. The
    `_SPEND_MAX_PAGES` ceiling is a defensive bound, not an expected
    operating regime.
    """
    total = Decimal("0")
    page_size = _SPEND_PAGE_SIZE
    for page in range(_SPEND_MAX_PAGES):
        start = page * page_size
        end = start + page_size - 1
        query = (
            client.table("active_transactions")
            .select("amount")
            .gte("date", window_start.isoformat())
            .lte("date", window_end.isoformat())
        )
        if category is not None:
            query = query.eq("category", category)
        resp = query.range(start, end).execute()
        rows = resp.data or []
        total += sum(
            (Decimal(str(row["amount"])) for row in rows), Decimal("0")
        )
        if len(rows) < page_size:
            return total
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "goal_spend_window_too_large",
            "message": (
                f"refusing to sum more than "
                f"{_SPEND_MAX_PAGES * page_size} rows for one goal window"
            ),
        },
    )


def _is_unique_violation(err: APIError) -> bool:
    """True if a PostgREST APIError wraps SQLSTATE 23505 (unique_violation)."""
    code = getattr(err, "code", None)
    if code == "23505":
        return True
    # supabase-py versions vary in where they stash the SQLSTATE; check
    # the message too as a defensive fallback.
    message = (getattr(err, "message", None) or str(err)).lower()
    return "23505" in message or "duplicate key" in message
