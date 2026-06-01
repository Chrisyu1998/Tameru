"""Auth routes — Day 7.

Three endpoints, all running before the single-active-device gate (the
gate compares X-Device-Id to users_meta.active_device_id, and these
routes are the ones that establish or query that value):

  - POST /auth/bootstrap   one-shot: writes the user's first users_meta
                            row with their chosen home_currency. 409 if
                            the row already exists. There is no surface
                            in v1 that mutates home_currency once set
                            (CLAUDE.md invariant 13); the DB trigger on
                            users_meta is the schema-level guarantee, this
                            endpoint's one-shot semantics are the API-level
                            mirror.
  - POST /auth/claim_device same returning user, new device. Updates
                            active_device_id only — never touches
                            home_currency. Displaces whichever device was
                            previously active.
  - GET  /auth/check_device frontend's idle-poll endpoint. Returns
                            is_active=true iff the supplied device_id
                            matches the row's active_device_id. Does NOT
                            401 on mismatch — the modal is driven by the
                            response body, not the status code, so a
                            failed match is observable without forcing a
                            sign-out flow on the poll itself.

The frontend dispatches on /me's `home_currency` field: null → bootstrap,
non-null → claim_device. See frontend/src/lib/auth.ts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError as PostgrestAPIError
from pydantic import BaseModel, Field, field_validator

from app.auth import AuthedUser, get_current_user_jwt
from app.db import supabase_for_user
from app.util.timezone import is_valid_timezone

router = APIRouter(prefix="/auth", tags=["auth"])

# Postgres SQLSTATEs we translate into structured HTTP errors on bootstrap.
# We don't keep a Python-side allowlist of currencies — CLAUDE.md invariant
# 13 says the migration's CHECK constraint is the single source of truth,
# and "do not add a separate allowlist in code". So we attempt the insert
# and translate `23514` (check violation → bad currency) and `23505`
# (unique violation → row already exists, including the race where two
# concurrent bootstraps both see no row in a pre-check) into structured
# 4xx responses. Anything else propagates.
_PG_CHECK_VIOLATION = "23514"
_PG_UNIQUE_VIOLATION = "23505"

_DEVICE_ID_MAX_LEN = 128


class BootstrapRequest(BaseModel):
    """Represent BootstrapRequest.

    `timezone` is the browser's detected IANA zone
    (`Intl.DateTimeFormat().resolvedOptions().timeZone`), optional and
    decoupled from `home_currency` (DESIGN.md §6.6). When omitted or
    invalid it stays NULL and the digest falls back to its default zone.
    """
    device_id: str = Field(min_length=1, max_length=_DEVICE_ID_MAX_LEN)
    home_currency: str
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _v_timezone(cls, value: str | None) -> str | None:
        """Reject a non-null timezone that isn't a real IANA zone (→ 422)."""
        if value is None:
            return None
        if not is_valid_timezone(value):
            raise ValueError("timezone is not a valid IANA zone")
        return value


class BootstrapResponse(BaseModel):
    """Represent BootstrapResponse."""
    home_currency: str
    active_device_id: str
    timezone: str | None = None


class ClaimDeviceRequest(BaseModel):
    """Represent ClaimDeviceRequest."""
    device_id: str = Field(min_length=1, max_length=_DEVICE_ID_MAX_LEN)


class ClaimDeviceResponse(BaseModel):
    """Represent ClaimDeviceResponse."""
    active_device_id: str


class CheckDeviceResponse(BaseModel):
    """Represent CheckDeviceResponse."""
    is_active: bool
    active_device_id: str | None
    # Reserved for a future "signed in on this device since X" UI; not
    # tracked as a column in v1, so always null today. Keeping the field
    # in the contract avoids a breaking response-shape change later.
    active_since: str | None


@router.post("/bootstrap", response_model=BootstrapResponse)
def bootstrap(
    body: BootstrapRequest,
    user: AuthedUser = Depends(get_current_user_jwt),
) -> BootstrapResponse:
    """One-shot insert of the user's `users_meta` row.

    Implementation: attempt the insert and let Postgres' constraints decide.
    Two error codes are translated to structured HTTP responses:

      - `23505` (unique_violation on the user_id PK) → 409 already_bootstrapped.
        Covers both the simple "second call" case and the concurrent-tab race
        where two requests both pre-checked an empty row.
      - `23514` (check_violation on home_currency) → 422 invalid_home_currency.

    No SELECT pre-check: it would only narrow the race window without
    closing it, and the unique-violation path is already the right
    answer in every case where the row exists.
    """
    client = supabase_for_user(user.jwt)
    insert_row = {
        "user_id": str(user.user_id),
        "active_device_id": body.device_id,
        "home_currency": body.home_currency,
    }
    # Only write timezone when the client supplied a valid one; NULL lets
    # the digest fall back to its default zone (DESIGN.md §6.4).
    if body.timezone is not None:
        insert_row["timezone"] = body.timezone
    try:
        ins = client.table("users_meta").insert(insert_row).execute()
    except PostgrestAPIError as exc:
        if exc.code == _PG_UNIQUE_VIOLATION:
            raise _domain_error(
                status.HTTP_409_CONFLICT,
                "already_bootstrapped",
                "users_meta already exists for this user; use /auth/claim_device",
            )
        if exc.code == _PG_CHECK_VIOLATION:
            raise _domain_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_home_currency",
                "home_currency is not in the supported set",
            )
        raise

    row = ins.data[0]
    return BootstrapResponse(
        home_currency=row["home_currency"],
        active_device_id=row["active_device_id"],
        timezone=row.get("timezone"),
    )


@router.post("/claim_device", response_model=ClaimDeviceResponse)
def claim_device(
    body: ClaimDeviceRequest,
    user: AuthedUser = Depends(get_current_user_jwt),
) -> ClaimDeviceResponse:
    """Provide claim device."""
    client = supabase_for_user(user.jwt)
    # Update-only — never touches home_currency. The trigger guarantees
    # the column would refuse a change anyway, but not sending it in the
    # update payload means we don't even attempt it.
    resp = (
        client.table("users_meta")
        .update({"active_device_id": body.device_id})
        .eq("user_id", str(user.user_id))
        .execute()
    )
    if not resp.data:
        # No row yet → caller should have hit /auth/bootstrap first. The
        # frontend's dispatch on /me.home_currency makes this a defensive
        # branch, not a normal flow.
        raise _domain_error(
            status.HTTP_409_CONFLICT,
            "not_bootstrapped",
            "users_meta missing for this user; call /auth/bootstrap first",
        )
    return ClaimDeviceResponse(active_device_id=resp.data[0]["active_device_id"])


@router.get("/check_device", response_model=CheckDeviceResponse)
def check_device(
    device_id: str = Query(min_length=1, max_length=_DEVICE_ID_MAX_LEN),
    user: AuthedUser = Depends(get_current_user_jwt),
) -> CheckDeviceResponse:
    """Provide check device."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("users_meta")
        .select("active_device_id")
        .eq("user_id", str(user.user_id))
        .execute()
    )
    if not resp.data:
        # Not yet bootstrapped — frontend treats this the same as a
        # mismatch (poll fires the modal). is_active=false is the right
        # signal, not a 4xx, because /auth/check_device is opt-in noise
        # the frontend swallows on its own schedule.
        return CheckDeviceResponse(is_active=False, active_device_id=None, active_since=None)
    active = resp.data[0].get("active_device_id")
    return CheckDeviceResponse(
        is_active=(active == device_id),
        active_device_id=active,
        active_since=None,
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _domain_error(http_status: int, code: str, message: str) -> HTTPException:
    """Support domain error."""
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})
