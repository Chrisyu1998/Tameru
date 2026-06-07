"""Issuer→region routing + region→source metadata — Tier 3 (DESIGN.md §6.6).

The backend mirror of the `card_issuers` reference table (migration
20260602120100). The DB table is the SQL-side source of truth; this module
is the code-side mirror the request path reads (no per-lookup DB round-trip),
the same "both layers must agree" pattern the `cards_issuer_check` CHECK ↔
`CardIssuer` Literal already follow. If the table's region/domain seed
changes, change the maps here in the same commit.

Reward lookup routes per card, not per user: a US card and a TW card can
live in one wallet and each resolves to its own sources + reward model. The
US path captures category multipliers; the JP/TW path captures a base earn
rate only (memory.md 2026-06-02 scope decision).
"""

from __future__ import annotations

from app.models.cards import CARD_LOOKUP_ALLOWED_DOMAINS, CardIssuer, CardRegion


__all__ = [
    "REGION_BY_ISSUER",
    "ISSUER_DOMAINS",
    "ALLOWED_DOMAINS_BY_REGION",
    "region_for_currency",
    "resolve_card_region",
]


# Issuer → region. Mirrors `card_issuers.region`. `other` is intentionally
# absent (it has no fixed region); callers fall back to home_currency.
REGION_BY_ISSUER: dict[str, CardRegion] = {
    # US
    "chase": "US",
    "amex": "US",
    "citi": "US",
    "capital_one": "US",
    "discover": "US",
    "bank_of_america": "US",
    "wells_fargo": "US",
    "usaa": "US",
    "bilt": "US",
    "barclays": "US",
    "us_bank": "US",
    "synchrony": "US",
    # JP
    "rakuten": "JP",
    "smbc": "JP",
    "jcb": "JP",
    "aeon": "JP",
    "epos": "JP",
    "saison": "JP",
    # TW
    "cathay": "TW",
    "esun": "TW",
    "ctbc": "TW",
    "taishin": "TW",
    "fubon": "TW",
    "union": "TW",
}


# Issuer → web domain. Mirrors `card_issuers.domain`. Used to widen the
# card-lookup `allowed_domains` allowlist with the issuer's own site.
ISSUER_DOMAINS: dict[str, str] = {
    # US
    "chase": "chase.com",
    "amex": "americanexpress.com",
    "citi": "citi.com",
    "capital_one": "capitalone.com",
    "discover": "discover.com",
    "bank_of_america": "bankofamerica.com",
    "wells_fargo": "wellsfargo.com",
    "usaa": "usaa.com",
    "bilt": "biltrewards.com",
    "barclays": "barclaysus.com",
    "us_bank": "usbank.com",
    "synchrony": "synchrony.com",
    # JP
    "rakuten": "rakuten-card.co.jp",
    "smbc": "smbc-card.com",
    "jcb": "jcb.co.jp",
    "aeon": "aeon.co.jp",
    "epos": "eposcard.co.jp",
    "saison": "saisoncard.co.jp",
    # TW
    "cathay": "cathaybk.com.tw",
    "esun": "esunbank.com.tw",
    "ctbc": "ctbcbank.com",
    "taishin": "taishinbank.com.tw",
    "fubon": "fubon.com",
    "union": "ubot.com.tw",
}


# Region → authoritative card-rewards source domains for the web_search
# allowlist. US reuses the existing curated set; JP/TW use the
# highest-confidence local card-comparison sources. The per-card issuer
# domain is appended on top at call time.
ALLOWED_DOMAINS_BY_REGION: dict[CardRegion, tuple[str, ...]] = {
    "US": CARD_LOOKUP_ALLOWED_DOMAINS,
    "JP": ("kakaku.com", "mybest.jp"),
    "TW": ("money101.com.tw", "rich01.com"),
}


# home_currency → region for the "issuer unknown / other" fallback. Only the
# two non-US currencies that map to a supported region need entries; every
# other home currency (USD, EUR, GBP, …) falls through to US sources, which
# is the correct default — a EUR user adding a card has no JP/TW source set.
_CURRENCY_TO_REGION: dict[str, CardRegion] = {
    "JPY": "JP",
    "TWD": "TW",
}


def region_for_currency(home_currency: str | None) -> CardRegion:
    """Best-effort region from a user's home currency.

    The fallback signal when no issuer-derived region is available (issuer
    is `other`, or at lookup time before the issuer is known). JPY→JP,
    TWD→TW, everything else→US.

    Example: `region_for_currency("TWD") == "TW"`.
    """
    if not home_currency:
        return "US"
    return _CURRENCY_TO_REGION.get(home_currency.upper(), "US")


def resolve_card_region(
    issuer: str | None,
    home_currency: str | None,
    requested_region: CardRegion | None = None,
) -> CardRegion:
    """Per-card region: issuer's region wins, then the user's explicit pick,
    then the home-currency fallback.

    Used at `/cards/confirm` to persist `cards.region`. Precedence:

      1. **Known issuer** → its pinned region, always. A US Amex is `US`
         even in a TWD-home wallet, and a client can't override it (forge-
         resistant — the meaningful identity case).
      2. **`other`/unknown issuer + explicit `requested_region`** → that
         region. This is the only lever a client controls, and it's the
         right one: an unenumerated issuer has no server-side region signal,
         so the user's add-card selection is the truth (e.g. a TWD user
         adding a small US-bank card they marked `US`).
      3. **Otherwise** → the home-currency guess.

    Example: `resolve_card_region("amex", "TWD", "TW") == "US"` (issuer wins);
             `resolve_card_region("other", "TWD", "US") == "US"` (explicit);
             `resolve_card_region("other", "TWD", None) == "TW"` (fallback).
    """
    if issuer:
        pinned = REGION_BY_ISSUER.get(issuer)
        if pinned is not None:
            return pinned
    if requested_region is not None:
        return requested_region
    return region_for_currency(home_currency)
