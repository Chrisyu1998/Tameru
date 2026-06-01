"""IANA timezone validation (DESIGN.md §6.6).

Shared by `/auth/bootstrap` and `PATCH /me/preferences`. Validation uses the
running Python's `zoneinfo` tz database — the same source `ZoneInfo()` reads
when the digest cron computes per-user week bounds — so "valid here" means
"the cron can resolve it." There is deliberately no DB CHECK constraint on
`users_meta.timezone`: the IANA zone set is large and version-dependent, so
the app layer is the authoritative validator (memory.md pattern: keep the
allowlist where the validator lives, not duplicated in a migration).
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
