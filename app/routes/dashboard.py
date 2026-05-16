"""Dashboard endpoints — Day 13 (DESIGN.md §6.2, §6.3).

`GET /dashboard/summary` is the single read powering the home screen
(UX frame 8). The route is intentionally thin: it delegates the
aggregation, gating, and prose generation to
`app.services.baselines.compute_dashboard_summary`, which in turn
calls the `dashboard_summary(p_today)` Postgres RPC for one round
trip of typed aggregates.

There is no `POST /dashboard/...` or write surface — the home screen
is read-only. Mutations flow through chat → propose → confirm
(CLAUDE.md invariant 8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import AuthedUser, get_current_user_with_device
from app.models.dashboard import DashboardSummary
from app.services.baselines import compute_dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    user: AuthedUser = Depends(get_current_user_with_device),
) -> DashboardSummary:
    """Return the home screen's aggregate spend + tile data for the caller.

    Response: `DashboardSummary` — top-level headline, observation
    sentence, and up to five category tiles. New users with no history
    get an empty `categories` list and `baseline_ready: false`.
    """
    return compute_dashboard_summary(user)
