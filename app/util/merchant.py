"""Merchant normalization — shared between Day 4 categorize() read path and
Day 5's merchant_category write path.

Must stay pure and cheap; it runs on the hot path for every transaction
entry and every past-corrections lookup.
"""

from __future__ import annotations

import re

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_merchant(raw: str) -> str:
    """Lowercase, strip, collapse interior whitespace.

    The merchant_category UNIQUE constraint is on (user_id, merchant), so
    this function's output is the key. Day 4 looks up past corrections by
    the normalized form; Day 5 writes corrections with the same form. Any
    divergence between the two would silently miss the past-corrections
    cache.
    """
    return _WHITESPACE_RUN.sub(" ", raw).strip().lower()
