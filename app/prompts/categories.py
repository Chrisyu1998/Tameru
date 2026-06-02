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
    "Memberships",     # Non-streaming recurring: software, gym, Patreon, news, cloud storage
    "Entertainment",   # Concerts, movies, events, venues, attractions
    "Shopping",        # General retail, clothing, electronics (including online)
    "Drugstores",      # CVS, Walgreens, Rite Aid
    "Home",            # Furniture, home improvement (Home Depot, IKEA)
    "Utilities",       # Electric, gas, water, internet, phone
    "Health",          # Doctor, dentist, vet, prescription copays, therapy
    "Other",           # Escape hatch — monitored as a signal in ai_call_log
)


# Localized *display* labels per category (DESIGN.md §6.6 Tier 2). The stored
# value is always the English enum above — the join key / glyph key /
# contract-test key. Only rendered text (e.g. the weekly digest's top-category
# line) is translated. Mirrors frontend/src/lib/categories.ts CATEGORY_LABELS;
# the two must stay in sync. `en` is the identity map so one lookup path covers
# every language. Traditional Chinese only. Drafts — native speakers refine.
CATEGORY_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "en": {c: c for c in ALLOWED_CATEGORIES},
    "ja": {
        "Groceries": "食料品",
        "Dining": "外食",
        "Coffee Shops": "カフェ",
        "Gas": "ガソリン",
        "Transit": "交通",
        "Travel": "旅行",
        "Streaming": "ストリーミング",
        "Memberships": "会員費",
        "Entertainment": "娯楽",
        "Shopping": "ショッピング",
        "Drugstores": "ドラッグストア",
        "Home": "住居",
        "Utilities": "公共料金",
        "Health": "健康",
        "Other": "その他",
    },
    "zh-TW": {
        "Groceries": "食品雜貨",
        "Dining": "餐飲",
        "Coffee Shops": "咖啡",
        "Gas": "加油",
        "Transit": "交通",
        "Travel": "旅遊",
        "Streaming": "串流",
        "Memberships": "會員",
        "Entertainment": "娛樂",
        "Shopping": "購物",
        "Drugstores": "藥妝店",
        "Home": "居家",
        "Utilities": "水電費",
        "Health": "健康",
        "Other": "其他",
    },
}


def category_display_label(category: str, ui_language: str | None) -> str:
    """Localized display label for `category` in `ui_language`.

    Falls back to English (the identity map) for an unset/unknown language or
    a category not in the enum, so the caller always gets a renderable string.
    """
    table = CATEGORY_DISPLAY_LABELS.get(ui_language or "en", CATEGORY_DISPLAY_LABELS["en"])
    return table.get(category, category)
