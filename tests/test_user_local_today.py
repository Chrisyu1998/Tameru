"""`user_local_today` — the shared user-timezone "today" helper (audit P2-6/P2-7).

Every read surface that windows on "today" (chat prompt date anchor,
dashboard p_today, spending-summary end-clamp, goal windows) resolves the
date through this helper, so its contract — user's IANA zone when set,
UTC fallback otherwise, never raises — is pinned here once rather than
per-surface.

Both tests restore `users_meta.timezone` in a finally block: user_a is a
session-scoped shared fixture, and leaving a throwaway zone behind would
poison later digest/preferences tests (memory.md 2026-06-07).
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

from app.util.timezone import user_local_today


def test_user_local_today_resolves_in_user_timezone(user_a, admin_client):
    """With a timezone set, the helper returns that zone's calendar date.

    Pacific/Kiritimati (UTC+14) maximizes the chance of differing from
    the UTC date, so a regression to server-UTC anchoring actually fails
    for most of each day instead of passing by coincidence.
    """
    prev = (
        admin_client.table("users_meta")
        .select("timezone")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    prev_tz = prev[0]["timezone"] if prev else None
    admin_client.table("users_meta").update(
        {"timezone": "Pacific/Kiritimati"}
    ).eq("user_id", user_a.id).execute()
    try:
        result = user_local_today(user_a.jwt)
        expected = _dt.datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
        assert result == expected
    finally:
        admin_client.table("users_meta").update({"timezone": prev_tz}).eq(
            "user_id", user_a.id
        ).execute()


def test_user_local_today_falls_back_to_utc_when_unset(user_a, admin_client):
    """NULL timezone → the UTC date (the pre-fix behavior, unchanged)."""
    prev = (
        admin_client.table("users_meta")
        .select("timezone")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    prev_tz = prev[0]["timezone"] if prev else None
    admin_client.table("users_meta").update({"timezone": None}).eq(
        "user_id", user_a.id
    ).execute()
    try:
        result = user_local_today(user_a.jwt)
        expected = _dt.datetime.now(_dt.timezone.utc).date()
        assert result == expected
    finally:
        admin_client.table("users_meta").update({"timezone": prev_tz}).eq(
            "user_id", user_a.id
        ).execute()
