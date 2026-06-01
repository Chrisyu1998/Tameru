"""Unit tests for app.util.timezone.is_valid_timezone (DESIGN.md §6.6).

Pure — no Supabase. Pins that the IANA validator accepts real zones
(including the JP/TW zones the v1 user base needs) and rejects empties,
non-strings, unknown names, and path-traversal-shaped junk without raising.
"""

from __future__ import annotations

import pytest

from app.util.timezone import is_valid_timezone


@pytest.mark.parametrize(
    "name",
    ["Asia/Tokyo", "Asia/Taipei", "America/New_York", "Europe/London", "UTC"],
)
def test_accepts_real_iana_zones(name: str) -> None:
    """Real IANA zones — including the JP/TW ones — validate as True."""
    assert is_valid_timezone(name) is True


@pytest.mark.parametrize(
    "value",
    ["", "   ", "Mars/Phobos", "Asia/Atlantis", "EST5EDT/bogus", "../etc/passwd", "GMT+abc"],
)
def test_rejects_invalid(value: str) -> None:
    """Empties, unknown zones, and malformed keys are rejected (no raise)."""
    assert is_valid_timezone(value) is False


def test_rejects_non_string() -> None:
    """A non-string never raises and is rejected."""
    assert is_valid_timezone(None) is False  # type: ignore[arg-type]
    assert is_valid_timezone(123) is False  # type: ignore[arg-type]
