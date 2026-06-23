"""Day 29 Tier 2 internationalization — backend surfaces (DESIGN.md §6.6).

Covers the pure pieces (language validation, category display labels, the
digest's localized email render + fallback narrative) and the DB-backed
chat reply-language directive. The frontend pieces (displayLocale, the
LanguageRow selector, category labels in the UI) are covered by Vitest.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from uuid import uuid4

from app.db import supabase_for_user
from app.prompts.categories import category_display_label
from app.prompts.chat import render_user_language
from app.services.digest import (
    CategoryRollup,
    DigestPayload,
    _fallback_narrative,
    _format_money,
    render_email,
)
from app.util.language import SUPPORTED_UI_LANGUAGES, is_valid_ui_language
import pytest

# Failure-path cleanup: restore user_a's shared users_meta prefs even when asserts fail (audit P3-37).
pytestmark = pytest.mark.usefixtures("preserve_user_a_meta")



def test_is_valid_ui_language_accepts_supported_and_rejects_others():
    """The validator admits exactly en/ja/zh-TW and nothing else — Simplified
    Chinese (zh-CN) is out of scope, so it must be rejected."""
    assert SUPPORTED_UI_LANGUAGES == ("en", "ja", "zh-TW")
    for code in SUPPORTED_UI_LANGUAGES:
        assert is_valid_ui_language(code) is True
    for bad in ("zh-CN", "ja-JP", "fr", "", "EN", "  ja  "):
        assert is_valid_ui_language(bad) is False


def test_category_display_label_translates_and_falls_back():
    """Stored English enum → localized label; unknown lang/category falls
    back to the English identity so the caller always gets a string."""
    assert category_display_label("Dining", "en") == "Dining"
    assert category_display_label("Dining", "ja") == "外食"
    assert category_display_label("Dining", "zh-TW") == "餐飲"
    # Unknown language → English identity.
    assert category_display_label("Dining", "fr") == "Dining"
    assert category_display_label("Dining", None) == "Dining"
    # Not-an-enum value passes through unchanged.
    assert category_display_label("Mystery", "ja") == "Mystery"


def test_fallback_narrative_localizes_by_language():
    """The deterministic Sonnet-outage fallback is written in the user's
    language so a provider failure still ships a same-language digest."""
    week, base = Decimal("100"), Decimal("0")
    assert "baseline" in _fallback_narrative(week, base, "en").lower()
    assert _fallback_narrative(week, base, "ja") == (
        "支出トラッキングの最初の週です。ここから基準ができていきます。"
    )
    assert _fallback_narrative(week, base, "zh-TW").startswith("這是開始記錄支出")
    # NULL/unknown → English (and the 2-arg legacy call still works).
    assert "baseline" in _fallback_narrative(week, base).lower()


def test_format_money_uses_currency_symbol_and_decimals():
    """The digest money helper renders the right symbol and fraction digits
    per currency — JPY is zero-decimal (¥1,500, not "JPY 1,500.00")."""
    assert _format_money(Decimal("1234.5"), "USD") == "$1,234.50"
    assert _format_money(Decimal("1500"), "JPY") == "¥1,500"
    assert _format_money(Decimal("500"), "TWD") == "NT$500.00"
    assert _format_money(Decimal("1234.56"), "EUR") == "€1,234.56"
    assert _format_money(Decimal("99"), "CHF") == "CHF 99.00"


def test_render_email_localizes_subject_and_body():
    """render_email renders every visible string in payload.ui_language: the
    Japanese email has a Japanese subject + body, the English one preserves
    the pre-Tier-2 copy verbatim. (Pure — no DB.)"""
    base = dict(
        user_id=uuid4(),
        week_start=_dt.datetime(2026, 4, 6),
        week_end=_dt.datetime(2026, 4, 12, 23, 59, 59),
        week_total=Decimal("420.00"),
        baseline_avg=Decimal("300.00"),
        top_category=CategoryRollup(
            category="Dining", week_total=Decimal("150.00"), baseline_avg=Decimal("100.00")
        ),
        observation="steady week",
        nudge=None,
    )

    en = render_email(
        DigestPayload(home_currency="USD", ui_language="en", **base),
        unsubscribe_url="https://x/u",
        app_cta_url="https://x/app",
    )
    assert en.subject == "Tameru — week of Apr 6–12"
    assert "You spent" in en.text
    assert "Dining" in en.text and "above" in en.text
    assert "Unsubscribe" in en.html

    ja = render_email(
        DigestPayload(home_currency="JPY", ui_language="ja", **base),
        unsubscribe_url="https://x/u",
        app_cta_url="https://x/app",
    )
    assert "振り返り" in ja.subject
    assert "先週の支出" in ja.text
    # Category label is localized in the top-category line.
    assert "外食" in ja.text
    # JPY amounts use ¥ and drop decimals (no "JPY 420.00").
    assert "¥420" in ja.text and "JPY" not in ja.text
    # Unsubscribe link text is localized.
    assert "配信停止" in ja.html

    tw = render_email(
        DigestPayload(home_currency="TWD", ui_language="zh-TW", **base),
        unsubscribe_url="https://x/u",
        app_cta_url="https://x/app",
    )
    assert "週回顧" in tw.subject
    assert "上週支出" in tw.text
    assert "餐飲" in tw.text


def test_render_user_language_directive_follows_ui_language(user_a):
    """The chat reply-language directive (chat_v12) reflects the user's
    ui_language, and is empty when unset (→ block[0]'s mirror-the-input
    fallback). DB-backed: writes users_meta.ui_language under the user's JWT."""
    db = supabase_for_user(user_a.jwt)

    db.table("users_meta").update({"ui_language": None}).eq(
        "user_id", user_a.id
    ).execute()
    assert render_user_language(user_a.jwt) == ""

    db.table("users_meta").update({"ui_language": "ja"}).eq(
        "user_id", user_a.id
    ).execute()
    directive = render_user_language(user_a.jwt)
    assert "Japanese" in directive and "Reply in Japanese" in directive

    # Cleanup for downstream session-scoped fixture reuse.
    db.table("users_meta").update({"ui_language": None}).eq(
        "user_id", user_a.id
    ).execute()
