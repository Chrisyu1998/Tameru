"""`app/util/merchant.py` contract — normalize_merchant().

Pure function but load-bearing: the merchant_category UNIQUE constraint is
on (user_id, merchant), and Day 4's read path and Day 5's write path both
key off this function's output. A divergence between any two call sites
silently misses the past-corrections cache without raising — which is why
explicit edge-case coverage at the unit level is worth it even though the
function is short.

We also assert idempotency (normalize(normalize(x)) == normalize(x)) so
calling it on already-normalized input is provably safe — the read path
sometimes does, e.g. when retrieving a stored merchant.
"""

from __future__ import annotations

import pytest

from app.util.merchant import normalize_merchant


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Already-normalized passes through unchanged.
        ("trader joes", "trader joes"),
        # Lowercase.
        ("TRADER JOES", "trader joes"),
        ("Trader Joes", "trader joes"),
        # Leading / trailing whitespace stripped.
        ("  Trader Joes  ", "trader joes"),
        # Interior whitespace runs collapse to a single space.
        ("Trader   Joes", "trader joes"),
        ("Trader\tJoes", "trader joes"),
        ("Trader\nJoes", "trader joes"),
        ("Trader \t \n Joes", "trader joes"),
        # Combined: leading + trailing + interior.
        ("\t  Trader   Joes \n ", "trader joes"),
        # Single-word merchants.
        ("Costco", "costco"),
        ("  COSTCO\t", "costco"),
        # Empty / whitespace-only collapses to empty. Day 5's API-layer
        # `_validate_merchant_nonblank` rejects this *before* normalization
        # ever runs on the write path; this assertion just pins down that
        # if it ever does run, the result is a stable empty string rather
        # than e.g. a single space.
        ("", ""),
        ("   ", ""),
        ("\t\n", ""),
        # Punctuation is preserved — the merchant_category cache treats
        # "Trader Joe's" and "Trader Joes" as distinct entries by design.
        # The normalization is whitespace + case only, never character
        # stripping.
        ("Trader Joe's", "trader joe's"),
        ("AT&T", "at&t"),
        ("7-Eleven", "7-eleven"),
        # Non-ASCII letters preserved (latin diacritics, ideographs). Day
        # 5's normalize step must not silently mangle these — a user with
        # "Café" expects their cache key to stay "café".
        ("Café  Du  Monde", "café du monde"),
        ("ラーメン  店", "ラーメン 店"),
    ],
)
def test_normalize_merchant(raw: str, expected: str) -> None:
    """Verify that normalize merchant."""
    assert normalize_merchant(raw) == expected


def test_normalize_merchant_is_idempotent() -> None:
    """Critical: normalize(normalize(x)) must equal normalize(x). Without
    this, calling the function on a value that was already normalized
    (e.g. fetched from the DB and re-queried) could shift the key and
    cause a cache miss."""
    samples = [
        "Trader Joes",
        "  Costco  ",
        "café  du  monde",
        "AT&T",
        "",
        "   ",
    ]
    for raw in samples:
        once = normalize_merchant(raw)
        twice = normalize_merchant(once)
        assert once == twice, f"non-idempotent on {raw!r}: {once!r} -> {twice!r}"
