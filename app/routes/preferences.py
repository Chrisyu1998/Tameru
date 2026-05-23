"""User preferences endpoint (Day 25 — `weekly_digest_enabled`).

Reads and updates the user-toggleable columns on `users_meta`.

Service-role usage forbidden here — preferences are user-owned data
and RLS does the work via the `users_meta_owner` policy
(`USING/CHECK user_id = auth.uid()`). The same boolean is also flipped
by the one-click unsubscribe route and the Resend webhook, which use
service role *because* they have no user JWT in scope (CLAUDE.md
invariant 1). All three paths converge on the same column.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user

router = APIRouter(prefix="/me", tags=["preferences"])


class PreferencesPatch(BaseModel):
    """Partial update for `users_meta` preference columns.

    Every field is optional; only set columns are written. Add new
    preference columns here as Settings grows — keep the surface tight
    so a future preference can't accidentally widen what's PATCHable
    without code review touching this model.

    `extra = forbid` to reject unknown keys at the API boundary — same
    posture as `PATCH /subscriptions/{id}` (memory.md 2026-05-19
    immutability rule).
    """
    weekly_digest_enabled: bool | None = Field(default=None)

    model_config = {"extra": "forbid"}


@router.patch("/preferences")
def patch_preferences(
    body: PreferencesPatch,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> dict[str, bool]:
    """Update one or more preference columns on the user's `users_meta` row.

    RLS owner-UPDATE policy scopes the write to `auth.uid() = user_id`
    automatically; a missing WHERE clause cannot leak. Returns the
    current values of all preference columns so the frontend can
    reconcile its optimistic UI in one round trip.
    """
    update_fields = body.model_dump(exclude_none=True)

    client = supabase_for_user(user.jwt)
    if update_fields:
        client.table("users_meta").update(update_fields).eq(
            "user_id", str(user.user_id)
        ).execute()

    # Read back the canonical state so the frontend can drop its
    # optimistic value and use the server's. Cheap at one row.
    resp = (
        client.table("users_meta")
        .select("weekly_digest_enabled")
        .eq("user_id", str(user.user_id))
        .execute()
    )
    row = resp.data[0] if resp.data else {}
    return {
        "weekly_digest_enabled": bool(row.get("weekly_digest_enabled", True)),
    }
