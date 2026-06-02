"""User preferences endpoint.

Reads and updates the user-toggleable columns on `users_meta`. v1 columns:
`weekly_digest_enabled` (Day 25), `analytics_opted_out` (Day 26), `timezone`
(Day 29) and `ui_language` (Day 29 Tier 2 — UI/display language, DESIGN.md
§6.6).

Service-role usage forbidden here — preferences are user-owned data
and RLS does the work via the `users_meta_owner` policy
(`USING/CHECK user_id = auth.uid()`). The `weekly_digest_enabled` boolean
is also flipped by the one-click unsubscribe route and the Resend
webhook, which use service role *because* they have no user JWT in
scope (CLAUDE.md invariant 1). All three paths converge on the same
column. `analytics_opted_out` has no service-role write path — only
the user can change it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.util.language import is_valid_ui_language
from app.util.timezone import is_valid_timezone

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
    analytics_opted_out: bool | None = Field(default=None)
    # IANA zone (DESIGN.md §6.6). Mutable, unlike home_currency. Validated
    # against zoneinfo; an invalid value is rejected at the API boundary
    # (422) rather than written and silently breaking the digest cron.
    timezone: str | None = Field(default=None)
    # UI/display language (DESIGN.md §6.6 Tier 2). Mutable. Validated against
    # the small fixed supported set; an invalid value is rejected at the API
    # boundary (422) rather than tripping the DB CHECK as an opaque 500.
    ui_language: str | None = Field(default=None)

    model_config = {"extra": "forbid"}

    @field_validator("timezone")
    @classmethod
    def _v_timezone(cls, value: str | None) -> str | None:
        """Reject a non-null timezone that isn't a real IANA zone (→ 422)."""
        if value is None:
            return None
        if not is_valid_timezone(value):
            raise ValueError("timezone is not a valid IANA zone")
        return value

    @field_validator("ui_language")
    @classmethod
    def _v_ui_language(cls, value: str | None) -> str | None:
        """Reject a non-null ui_language outside the supported set (→ 422)."""
        if value is None:
            return None
        if not is_valid_ui_language(value):
            raise ValueError("ui_language is not in the supported set")
        return value


class PreferencesResponse(BaseModel):
    """Canonical state of every preference column after the write.

    Returned by both PATCH and the empty-body PATCH used by the frontend
    as a read endpoint. Pydantic models the shape explicitly so the
    contract is part of the type system instead of a loose dict.
    """
    weekly_digest_enabled: bool
    analytics_opted_out: bool
    timezone: str | None = None
    ui_language: str | None = None


@router.patch("/preferences")
def patch_preferences(
    body: PreferencesPatch,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> PreferencesResponse:
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
        .select(
            "weekly_digest_enabled, analytics_opted_out, timezone, ui_language"
        )
        .eq("user_id", str(user.user_id))
        .execute()
    )
    row = resp.data[0] if resp.data else {}
    return PreferencesResponse(
        weekly_digest_enabled=bool(row.get("weekly_digest_enabled", True)),
        analytics_opted_out=bool(row.get("analytics_opted_out", False)),
        timezone=row.get("timezone"),
        ui_language=row.get("ui_language"),
    )
