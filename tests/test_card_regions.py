"""Unit tests for Tier 3 region resolution (DESIGN.md §6.6).

Pins the precedence in `resolve_card_region` — the logic behind the
per-card mixed-wallet region. Pure functions, no DB / fixtures.
"""

from app.integrations.card_regions import (
    region_for_currency,
    resolve_card_region,
)


def test_region_for_currency_maps_jpy_and_twd():
    """JPY→JP, TWD→TW; every other currency (and None) falls back to US."""
    assert region_for_currency("JPY") == "JP"
    assert region_for_currency("TWD") == "TW"
    assert region_for_currency("USD") == "US"
    assert region_for_currency("EUR") == "US"
    assert region_for_currency(None) == "US"


def test_known_issuer_region_is_pinned_over_currency_and_request():
    """A known issuer's region wins over both home_currency and an explicit
    request — a US Amex stays US in a TWD wallet, and a forged request can't
    relabel it."""
    assert resolve_card_region("amex", "TWD", "TW") == "US"
    assert resolve_card_region("rakuten", "USD", "US") == "JP"
    assert resolve_card_region("cathay", "USD", None) == "TW"


def test_other_issuer_honors_explicit_requested_region():
    """The P2 fix: an unenumerated (`other`) issuer with an explicit region
    selection persists that region, not the home-currency guess. This keeps
    the add-card override working for a TWD user adding a small US-bank card."""
    assert resolve_card_region("other", "TWD", "US") == "US"
    assert resolve_card_region("other", "USD", "JP") == "JP"
    assert resolve_card_region(None, "TWD", "US") == "US"


def test_other_issuer_without_request_falls_back_to_currency():
    """No issuer signal and no explicit pick → the home-currency guess (the
    chat path, which has no region selector)."""
    assert resolve_card_region("other", "TWD", None) == "TW"
    assert resolve_card_region("other", "JPY", None) == "JP"
    assert resolve_card_region("other", "USD", None) == "US"
