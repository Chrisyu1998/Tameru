"""Per-user timezone behavior for the weekly digest (DESIGN.md §6.6).

Pure — no Supabase. Covers the two timezone-sensitive pieces added in Day 29:

  * `_is_within_send_window` (cron): the hourly-fire gate that lets each user
    receive the digest at ~09:00 in their own zone.
  * `_previous_week_bounds` / `_resolve_tz` (service): week-boundary math in
    the user's zone, with a safe fallback for NULL/invalid zones.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.cron.digest import _is_within_send_window, _local_week_monday
from app.services.digest import (
    DEFAULT_DIGEST_TZ_NAME,
    _previous_week_bounds,
    _resolve_tz,
)

# 2026-06-01 00:00 UTC. Guarded below to be a Monday so the window cases
# read clearly; June → US Eastern is on EDT (UTC-4).
_MONDAY_UTC_MIDNIGHT = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def test_base_instant_is_a_monday() -> None:
    """Guard: the fixture instant must be Monday 00:00 UTC for the cases below."""
    assert _MONDAY_UTC_MIDNIGHT.weekday() == 0


def test_tokyo_user_is_in_window_at_utc_monday_midnight() -> None:
    """Asia/Tokyo (UTC+9): Monday 00:00 UTC is Monday 09:00 JST → in window."""
    assert _is_within_send_window("Asia/Tokyo", _MONDAY_UTC_MIDNIGHT) is True


def test_taipei_user_is_not_yet_in_window_at_utc_monday_midnight() -> None:
    """Asia/Taipei (UTC+8): 00:00 UTC is 08:00 — one hour early; the next
    hourly fire (01:00 UTC = 09:00 CST) is when Taipei lands in the window."""
    assert _is_within_send_window("Asia/Taipei", _MONDAY_UTC_MIDNIGHT) is False
    one_hour_later = _MONDAY_UTC_MIDNIGHT + timedelta(hours=1)
    assert _is_within_send_window("Asia/Taipei", one_hour_later) is True


def test_eastern_user_in_window_at_utc_monday_13() -> None:
    """America/New_York on EDT (UTC-4): 13:00 UTC is Monday 09:00 ET → in window."""
    et_nine = _MONDAY_UTC_MIDNIGHT + timedelta(hours=13)
    assert _is_within_send_window("America/New_York", et_nine) is True


def test_retry_window_covers_9_10_11_but_not_noon_or_8() -> None:
    """The send window is Monday [09:00, 12:00): 9/10/11 in, 8 and 12 out.
    This is the retry budget — a failed 09:00 send gets re-attempted at
    10:00 and 11:00; noon is the cutoff (DESIGN.md §6.6)."""
    tz = "Asia/Tokyo"  # UTC+9: UTC midnight = 09:00 JST
    in_hours = {9: 0, 10: 1, 11: 2}
    for local_hour, utc_offset in in_hours.items():
        when = _MONDAY_UTC_MIDNIGHT + timedelta(hours=utc_offset)
        assert _is_within_send_window(tz, when) is True, f"{local_hour}:00 should be in window"
    # 08:00 JST (one hour before the window) and 12:00 JST (the cutoff) are out.
    assert _is_within_send_window(tz, _MONDAY_UTC_MIDNIGHT - timedelta(hours=1)) is False
    assert _is_within_send_window(tz, _MONDAY_UTC_MIDNIGHT + timedelta(hours=3)) is False


def test_null_timezone_falls_back_to_default_zone() -> None:
    """A user with no zone (None) is gated on the default zone, not skipped
    forever — preserves the historical 09:00-ET delivery."""
    et_nine = _MONDAY_UTC_MIDNIGHT + timedelta(hours=13)
    assert _is_within_send_window(None, et_nine) is True
    # ...and is out of window at a non-ET-9am instant.
    assert _is_within_send_window(None, _MONDAY_UTC_MIDNIGHT) is False


def test_invalid_timezone_falls_back_without_raising() -> None:
    """An unresolvable stored zone must not crash the gate."""
    et_nine = _MONDAY_UTC_MIDNIGHT + timedelta(hours=13)
    assert _is_within_send_window("Mars/Phobos", et_nine) is True


def test_resolve_tz_defaults_on_null_and_garbage() -> None:
    """_resolve_tz returns the default zone for None and invalid names."""
    default = ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
    assert _resolve_tz(None) == default
    assert _resolve_tz("Not/AZone") == default
    assert _resolve_tz("Asia/Tokyo") == ZoneInfo("Asia/Tokyo")


def test_local_week_monday_is_stable_across_sydney_retry_fires() -> None:
    """The dedup key (local Monday date) is identical for Sydney's 09:00 /
    10:00 / 11:00 fires even though they straddle the UTC week boundary
    (09:00 Sydney = Sunday 23:00 UTC). This is the invariant the local-week
    dedup relies on to stop a double-send for zones east of UTC+9
    (DESIGN.md §6.6)."""
    tz = "Australia/Sydney"
    keys = {
        _local_week_monday(tz, _MONDAY_UTC_MIDNIGHT - timedelta(hours=1)),  # 09:00 Sydney
        _local_week_monday(tz, _MONDAY_UTC_MIDNIGHT),                       # 10:00 Sydney
        _local_week_monday(tz, _MONDAY_UTC_MIDNIGHT + timedelta(hours=1)),  # 11:00 Sydney
    }
    assert len(keys) == 1, f"Sydney retry fires must share one dedup key, got {keys}"


def test_local_week_monday_agrees_across_zones_in_the_same_week() -> None:
    """Two users in different zones during the same week resolve to the same
    local Monday date — so a mid-week tz change can't mint a second key."""
    # 10:00 UTC Monday: well inside Monday for every zone here.
    when = _MONDAY_UTC_MIDNIGHT + timedelta(hours=10)
    keys = {
        _local_week_monday(z, when)
        for z in ("America/Los_Angeles", "America/Chicago", "Asia/Tokyo", "Asia/Taipei")
    }
    assert keys == {"2026-06-01"}


def test_local_week_monday_falls_back_on_bad_zone() -> None:
    """None / invalid zone resolves via the default zone without raising."""
    assert _local_week_monday(None, _MONDAY_UTC_MIDNIGHT + timedelta(hours=13))  # ET Monday 09:00
    assert _local_week_monday("Mars/Phobos", _MONDAY_UTC_MIDNIGHT + timedelta(hours=13))


def test_previous_week_bounds_are_a_full_prior_monday_to_sunday() -> None:
    """Bounds are Monday 00:00 → the following Sunday 23:59:59.999999, and
    the whole window is in the past (the just-ended week)."""
    tz = ZoneInfo("Asia/Tokyo")
    start, end = _previous_week_bounds(tz)
    assert start.weekday() == 0  # Monday
    assert end.weekday() == 6  # Sunday
    # Span is one microsecond short of 7 full days.
    assert (end - start) == timedelta(days=7) - timedelta(microseconds=1)
    # The summarized week has fully ended relative to now in that zone.
    assert end < datetime.now(tz)
