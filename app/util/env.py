"""APP_ENV normalization — one definition of "are we in production?".

Before this module, three call sites each parsed APP_ENV with different
defaults: `app.main`'s lifespan gates treated unset as non-production,
`app.sentry_filters` labeled unset as production, and
`app.logging_config` compared against "dev" (2026-06 audit P3-7). A
typo'd value ("producton") silently skipped the production fail-fast
tier. All APP_ENV reads now go through here, and `app.main`'s lifespan
rejects unrecognized values at boot.
"""

from __future__ import annotations

import os

# The values the deploy surfaces actually use: Railway prod sets
# "production"; local dev either leaves APP_ENV unset or sets "dev".
# The synonyms cost nothing to accept and turn a plausible operator
# spelling into a non-event instead of a boot failure.
KNOWN_APP_ENVS = frozenset(
    {"", "dev", "development", "test", "staging", "production"}
)


def app_env() -> str:
    """Return the normalized (stripped, lowercased) APP_ENV value.

    Unset resolves to "" — callers needing a boolean use
    `is_production()`; callers needing a label decide their own default.
    """
    return os.environ.get("APP_ENV", "").strip().lower()


def is_production() -> bool:
    """True iff APP_ENV resolves to exactly "production".

    Unset/unknown values are non-production: the production-only checks
    this gates (required-env tier, Sentry DSN presence) must never fire
    on a dev machine, and the lifespan's `KNOWN_APP_ENVS` validation is
    what catches a typo'd production value instead of a silent skip.
    """
    return app_env() == "production"
