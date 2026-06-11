"""IANA timezone validation + user-local "today" (DESIGN.md §6.6).

Shared by `/auth/bootstrap` and `PATCH /me/preferences`. Validation uses the
running Python's `zoneinfo` tz database — the same source `ZoneInfo()` reads
when the digest cron computes per-user week bounds — so "valid here" means
"the cron can resolve it." There is deliberately no DB CHECK constraint on
`users_meta.timezone`: the IANA zone set is large and version-dependent, so
the app layer is the authoritative validator (memory.md pattern: keep the
allowlist where the validator lives, not duplicated in a migration).

`user_local_today` is the single source of the user's calendar date for
every read surface that windows on "today" (chat prompt date anchor,
dashboard `p_today`, spending-summary end-clamp, goal windows). Server-UTC
`date.today()` is wrong for east-of-UTC users for the first 8–9 local
hours of every day — their just-confirmed local-today rows fall outside a
UTC-anchored window, and at month boundaries the dashboard shows the
previous month as "this month" (2026-06 audit P2-6/P2-7).
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import supabase_for_user


def user_local_today(user_jwt: str) -> _dt.date:
    """Return "today" in the caller's `users_meta.timezone`.

    Request shape: the caller's JWT — the `users_meta` read runs under
    RLS, so only the caller's own row is visible.
    Response shape: `datetime.date` — today in the user's IANA zone, or
    the UTC date when the timezone is unset, invalid, or the read fails.

    The UTC fallback (not America/New_York, the digest's fallback) is
    deliberate: it preserves the pre-fix behavior for users with no
    timezone, so nothing shifts for them. Never raises — a transient DB
    error degrades to the UTC date rather than failing the read surface.
    """
    try:
        client = supabase_for_user(user_jwt)
        rows = (
            client.table("users_meta")
            .select("timezone")
            .execute()
            .data
            or []
        )
        name = rows[0].get("timezone") if rows else None
    except Exception:  # noqa: BLE001 — degrade to UTC, never fail the caller
        name = None
    if name and is_valid_timezone(name):
        return _dt.datetime.now(ZoneInfo(name)).date()
    return _dt.datetime.now(_dt.timezone.utc).date()


def is_valid_timezone(name: str) -> bool:
    """True iff `name` resolves to a real IANA zone via `zoneinfo`.

    Request shape: a candidate timezone string (e.g. "Asia/Tokyo").
    Response shape: bool — True when `ZoneInfo(name)` succeeds.

    Returns False for empties, non-strings, unknown zones
    (`ZoneInfoNotFoundError`), and malformed keys such as paths containing
    ".." or null bytes (`ValueError`) — never raises.
    """
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
