"""UI-language validation (DESIGN.md §6.6 Tier 2).

Shared by `/auth/bootstrap` and `PATCH /me/preferences`. Unlike timezone (a
large, version-dependent IANA set validated against `zoneinfo`), the supported
UI-language set is small and fixed, so it lives as an explicit tuple here and
is mirrored by a CHECK constraint on `users_meta.ui_language` (migration
`20260601140000`). The two must stay in sync — the set is small and stable, so
the duplication is cheap and the CHECK is the DB-layer backstop while this
constant gives the API a clean 422 without round-tripping a 23514.

`zh-TW` is Traditional Chinese only — Simplified is out of scope for v1
(DESIGN.md §6.6).
"""

from __future__ import annotations

# Canonical supported set. Mirrors the CHECK on users_meta.ui_language.
SUPPORTED_UI_LANGUAGES: tuple[str, ...] = ("en", "ja", "zh-TW")


def is_valid_ui_language(value: str) -> bool:
    """True iff `value` is one of the supported UI-language codes.

    Request shape: a candidate language code (e.g. "ja").
    Response shape: bool — True when `value` is in `SUPPORTED_UI_LANGUAGES`.

    Returns False for empties, non-strings, and unknown codes; never raises.
    """
    if not value or not isinstance(value, str):
        return False
    return value in SUPPORTED_UI_LANGUAGES
