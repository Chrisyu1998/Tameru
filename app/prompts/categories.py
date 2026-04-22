"""Closed category enum — single source of truth for both the Gemini prompt
(Day 4) and response validation.

Taxonomy v2 is aligned with the merchant-category-code groupings that
drive credit card reward multipliers — because a core Tameru insight is
"which card earns most on this category" (§6.2 Entry-Moment Insight,
card-mismatch rule). Fragmenting this set to match card rewards is what
makes those insights possible. Plaid's PFC taxonomy was the other
reference point.

If you add, remove, or rename a category, bump PROMPT_VERSION in
categorize.py. Eval regressions and ai_call_log.prompt_hash both rely on
that bump to line up with a distinct prompt shape.
"""

from __future__ import annotations

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "Groceries",       # Supermarkets, produce markets, corner stores
    "Dining",          # Restaurants, bars, fast food, food delivery
    "Coffee Shops",    # Starbucks, Blue Bottle, independent cafes, tea shops
    "Gas",             # Gas stations for vehicles
    "Transit",         # Rideshare (Uber/Lyft), public transit, parking, tolls
    "Travel",          # Airlines, hotels, car rentals, cruises
    "Streaming",       # Netflix, Spotify, Apple Music, Hulu, YouTube Premium
    "Subscriptions",   # Non-streaming recurring: software, gym, Patreon, news
    "Entertainment",   # Concerts, movies, events, venues, attractions
    "Shopping",        # General retail, clothing, electronics (including online)
    "Drugstores",      # CVS, Walgreens, Rite Aid
    "Home",            # Furniture, home improvement (Home Depot, IKEA)
    "Utilities",       # Electric, gas, water, internet, phone
    "Health",          # Doctor, dentist, vet, prescription copays, therapy
    "Other",           # Escape hatch — monitored as a signal in ai_call_log
)
