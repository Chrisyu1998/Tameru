"""Goals REST endpoints — read, edit, delete.

Three operations, all RLS-scoped via the user's JWT (CLAUDE.md invariant
#1). Creation deliberately stays in chat via the `set_goal` agent tool
(CLAUDE.md invariant #8's lone direct-write carve-out); these HTTP
routes cover the read + mutation surface the `/goals` page needs.

  * `GET /goals`            — list the user's goals with per-goal
                              period-to-date spend. The frontend renders
                              progress bars off the spend ratio without
                              a second round-trip.
  * `PATCH /goals/{id}`     — update amount and/or period in place.
                              Category is fixed by the unique key — to
                              move a goal between categories, delete it
                              and ask chat to set a new one.
  * `DELETE /goals/{id}`    — hard-delete. Matches `/memory` semantics:
                              no FK from transactions to goals, so no
                              soft-delete tombstone is needed.

The `/goals` page surfaces these; the breakdown page also reads `GET
/goals` for its month-scoped progress strip.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.auth import AuthedUser, get_current_user_with_device
from app.models.goals import Goal, GoalPatchRequest, GoalsListResponse
from app.services.goals import delete_goal, list_goals_with_spend, update_goal

router = APIRouter(prefix="/goals", tags=["goals"])


@router.get("", response_model=GoalsListResponse)
def get_goals(
    user: AuthedUser = Depends(get_current_user_with_device),
) -> GoalsListResponse:
    """Return the user's goals with calendar-aligned period-to-date spend.

    Response:
        `{items: [{goal, spent_period_to_date, window_start, window_end,
        progress_ratio}, ...]}`. Empty list is a normal response for a
        new user; the `/goals` page renders an empty-state CTA.

    Spend is computed server-side so the client does not need to issue a
    per-goal `calculate_total` call. Period windows are calendar-aligned
    (week = Mon..Sun, month = 1st..last, year = Jan 1..Dec 31).
    """
    return list_goals_with_spend(user)


@router.patch("/{goal_id}", response_model=Goal)
def patch_goal(
    goal_id: UUID,
    patch: GoalPatchRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Goal:
    """Update amount and/or period on the user's goal.

    Request:
        `{amount?: number, period?: "week"|"month"|"year"}` — at least
        one of the two is required (422 otherwise).

    Response:
        The updated `Goal` row.

    A 404 means the row doesn't exist or belongs to another user (RLS
    hides the distinction). A 409 with detail
    `{code: "goal_slot_occupied", ...}` means the requested period would
    collide with an existing goal in the same `(user, category, period)`
    slot — the frontend surfaces this inline on the edit sheet.
    """
    return update_goal(user, goal_id, patch)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_goal(
    goal_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Hard-delete a goal owned by the user.

    Idempotent: RLS makes "delete nonexistent" and "delete someone
    else's row" indistinguishable from the caller's seat — both return
    204 without a write.
    """
    delete_goal(user, goal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
