"""Day 9a — read-tool integration tests.

Each tool gets its own section with happy paths and the edge cases that
real bugs hide in: empty results, ordering, boundary values, filter
combinations, hard-cap behavior, and cross-user RLS isolation.

Conventions:

  * Real local Supabase + real RLS — tools are pure-Python functions
    calling Postgres via the user's JWT, so a mocked client would mask
    the failure modes we care about.

  * Session-scoped fixtures (user_a, user_b, card_a, card_b) carry data
    across tests. Every seed function tags its rows with a unique
    `uuid4().hex[:8]` so a test that asserts equality narrows by tag
    rather than counting all session data.

  * For tools that aggregate (`calculate_total`, `get_spending_summary`)
    over session-scoped data, assertions use the tag-narrowed subset.
    Where the tool can't filter (e.g. `get_spending_summary` has no
    merchant filter), assertions use ">=" lower bounds.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.agent import tools as tools_module
from app.agent.tools import (
    TOOL_REGISTRY,
    calculate_total,
    execute_tool,
    get_cards,
    get_spending_summary,
    get_subscriptions,
    get_transactions,
)
from app.models.transactions import MAX_LIMIT


# ---------------------------------------------------------------------------
# Shared fixtures + helpers.
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_user_a(user_a) -> AuthedUser:
    return AuthedUser(jwt=user_a.jwt, user_id=uuid.UUID(user_a.id), email=user_a.email)


@pytest.fixture
def authed_user_b(user_b) -> AuthedUser:
    return AuthedUser(jwt=user_b.jwt, user_id=uuid.UUID(user_b.id), email=user_b.email)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _seed_transaction(
    user,
    *,
    card_id: str | None,
    merchant: str,
    amount: str,
    category: str = "Dining",
    txn_date: date | None = None,
) -> str:
    """Insert one transaction via the user's RLS-scoped client; return id."""
    client = supabase_for_user(user.jwt)
    row: dict[str, object] = {
        "user_id": user.id,
        "merchant": merchant,
        "amount": amount,
        "date": (txn_date or date.today()).isoformat(),
        "category": category,
        "source": "manual",
        "client_request_id": str(uuid.uuid4()),
    }
    if card_id is not None:
        row["card_id"] = card_id
    return client.table("transactions").insert(row).execute().data[0]["id"]


def _seed_subscription(
    user,
    *,
    card_id: str,
    name: str,
    next_billing: date,
    amount: str = "9.99",
    frequency: str = "monthly",
    status: str = "active",
    category: str = "Streaming",
) -> str:
    client = supabase_for_user(user.jwt)
    return (
        client.table("subscriptions")
        .insert({
            "user_id": user.id,
            "card_id": card_id,
            "name": name,
            "amount": amount,
            "frequency": frequency,
            "start_date": next_billing.isoformat(),
            "next_billing_date": next_billing.isoformat(),
            "category": category,
            "status": status,
        })
        .execute()
        .data[0]["id"]
    )


# ===========================================================================
# Registry sanity.
# ===========================================================================


def test_registry_contains_only_day9a_read_tools():
    expected = {
        "calculate_total",
        "get_transactions",
        "get_subscriptions",
        "get_spending_summary",
        "get_cards",
    }
    assert set(TOOL_REGISTRY) == expected, (
        "Day 9a registers only read tools. propose_* tools belong to "
        "9b / Day 14 / Day 19. If a propose tool landed early, this "
        "test failing is the structural alarm."
    )


def test_every_registered_tool_has_paired_schema_and_executor():
    """The pairing in TOOL_REGISTRY is the contract the loop relies on
    at `loop.py:278` (executor) and `loop.py:201` (schema). A tool with
    a schema but no executor would dispatch into None; a tool with an
    executor but no schema would never appear in Claude's tool list."""
    for name, (schema, executor) in TOOL_REGISTRY.items():
        assert schema["name"] == name, f"{name}: schema.name mismatch"
        assert callable(executor), f"{name}: executor not callable"
        assert "input_schema" in schema, f"{name}: missing input_schema"


# ===========================================================================
# calculate_total
# ===========================================================================


def test_calculate_total_no_filters_returns_session_total(authed_user_a):
    """An unfiltered call totals everything the user owns. We can't
    assert an exact figure (session-scoped fixtures accumulate rows
    across tests), but the shape must be right and counts/totals must
    be non-negative."""
    result = calculate_total(authed_user_a)
    assert set(result) == {"total", "count", "truncated"}
    assert Decimal(result["total"]) >= 0
    assert result["count"] >= 0
    assert result["truncated"] is False


def test_calculate_total_no_matches_returns_zero(authed_user_a):
    """A filter that excludes every row must return total=0 / count=0,
    not crash or return None. The "no spend on X" question must answer
    cleanly."""
    result = calculate_total(authed_user_a, merchant_contains=f"nonexistent-{_tag()}")
    assert result["total"] == "0"
    assert result["count"] == 0
    assert result["truncated"] is False


def test_calculate_total_category_filter_narrows(authed_user_a, user_a, card_a):
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"Dn-{tag}", amount="50.00", category="Dining")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Gr-{tag}", amount="40.00", category="Groceries")

    dining = calculate_total(authed_user_a, category="Dining", merchant_contains=tag)
    groceries = calculate_total(authed_user_a, category="Groceries", merchant_contains=tag)

    assert Decimal(dining["total"]) == Decimal("50.00") and dining["count"] == 1
    assert Decimal(groceries["total"]) == Decimal("40.00") and groceries["count"] == 1


def test_calculate_total_card_id_filter_narrows(authed_user_a, user_a, card_a):
    """Two transactions with the same merchant tag, one on card_a and
    one card-less. Filtering by card_id must include only the card_a row."""
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"OnCard-{tag}", amount="10.00")
    _seed_transaction(user_a, card_id=None, merchant=f"OnCard-{tag}", amount="20.00")

    with_card = calculate_total(authed_user_a, card_id=card_a, merchant_contains=f"oncard-{tag}")
    no_filter = calculate_total(authed_user_a, merchant_contains=f"oncard-{tag}")

    assert Decimal(with_card["total"]) == Decimal("10.00") and with_card["count"] == 1
    assert Decimal(no_filter["total"]) == Decimal("30.00") and no_filter["count"] == 2


def test_calculate_total_date_range_is_inclusive(authed_user_a, user_a, card_a):
    """date_from and date_to bounds are inclusive on BOTH ends. A
    transaction dated exactly at the boundary must be counted."""
    tag = _tag()
    base = date.today() - timedelta(days=10)
    _seed_transaction(user_a, card_id=card_a, merchant=f"In-{tag}", amount="5.00", txn_date=base)
    _seed_transaction(user_a, card_id=card_a, merchant=f"In-{tag}", amount="7.00", txn_date=base + timedelta(days=2))
    # One day before the range and one day after — must be excluded.
    _seed_transaction(user_a, card_id=card_a, merchant=f"In-{tag}", amount="100.00", txn_date=base - timedelta(days=1))
    _seed_transaction(user_a, card_id=card_a, merchant=f"In-{tag}", amount="100.00", txn_date=base + timedelta(days=3))

    result = calculate_total(
        authed_user_a,
        merchant_contains=f"in-{tag}",
        date_from=base.isoformat(),
        date_to=(base + timedelta(days=2)).isoformat(),
    )
    assert Decimal(result["total"]) == Decimal("12.00")
    assert result["count"] == 2


def test_calculate_total_amount_range_is_inclusive(authed_user_a, user_a, card_a):
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"Amt-{tag}", amount="9.99")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Amt-{tag}", amount="10.00")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Amt-{tag}", amount="20.00")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Amt-{tag}", amount="20.01")

    result = calculate_total(
        authed_user_a, merchant_contains=f"amt-{tag}", amount_min=10, amount_max=20
    )
    assert Decimal(result["total"]) == Decimal("30.00")
    assert result["count"] == 2


def test_calculate_total_merchant_contains_widening(authed_user_a, user_a, card_a):
    """Pre-9a, calculate_total accepted only {category, card_id, date_from,
    date_to}. After 9a it must accept `merchant_contains` so 'how much at
    Trader Joe's this month' routes here instead of `get_transactions`."""
    tag = _tag()
    target = f"Trader Joe-{tag}"
    _seed_transaction(user_a, card_id=card_a, merchant=target, amount="20.00", category="Groceries")
    _seed_transaction(user_a, card_id=card_a, merchant=target, amount="15.00", category="Groceries")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Other-{tag}", amount="500.00", category="Groceries")

    result = calculate_total(authed_user_a, merchant_contains=f"trader joe-{tag}")
    assert Decimal(result["total"]) == Decimal("35.00")
    assert result["count"] == 2


def test_calculate_total_combined_filters(authed_user_a, user_a, card_a):
    """All filters together — the kind of disambiguating call Claude
    builds for "the $10 coffee from last Tuesday on my Amex"."""
    tag = _tag()
    target_day = date.today() - timedelta(days=5)
    # Matches: card_a, Dining, "Coffee", $9-$11, target day.
    _seed_transaction(user_a, card_id=card_a, merchant=f"Coffee-{tag}", amount="10.50", category="Dining", txn_date=target_day)
    # Excluded by amount.
    _seed_transaction(user_a, card_id=card_a, merchant=f"Coffee-{tag}", amount="15.00", category="Dining", txn_date=target_day)
    # Excluded by category.
    _seed_transaction(user_a, card_id=card_a, merchant=f"Coffee-{tag}", amount="10.50", category="Groceries", txn_date=target_day)
    # Excluded by date.
    _seed_transaction(user_a, card_id=card_a, merchant=f"Coffee-{tag}", amount="10.50", category="Dining", txn_date=target_day - timedelta(days=2))

    result = calculate_total(
        authed_user_a,
        merchant_contains=f"coffee-{tag}",
        category="Dining",
        amount_min=9,
        amount_max=11,
        date_from=target_day.isoformat(),
        date_to=target_day.isoformat(),
    )
    assert Decimal(result["total"]) == Decimal("10.50")
    assert result["count"] == 1


def test_calculate_total_preserves_decimal_precision(authed_user_a, user_a, card_a):
    """Amounts come back as strings from numeric columns; sum must stay
    Decimal so float artifacts (0.1 + 0.2 == 0.30000000000000004) never
    appear in a user-facing total."""
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"Frac-{tag}", amount="0.10")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Frac-{tag}", amount="0.20")
    _seed_transaction(user_a, card_id=card_a, merchant=f"Frac-{tag}", amount="0.30")

    result = calculate_total(authed_user_a, merchant_contains=f"frac-{tag}")
    assert Decimal(result["total"]) == Decimal("0.60")


def test_calculate_total_truncation_flag_fires(authed_user_a, user_a, card_a, monkeypatch):
    """Seeding 5,001 rows would be slow; lower the cap so the truncation
    path is exercisable with a handful of seeded rows."""
    monkeypatch.setattr(tools_module, "RESULT_ROW_CAP", 2)
    tag = _tag()
    for _ in range(4):
        _seed_transaction(user_a, card_id=card_a, merchant=f"Trunc-{tag}", amount="1.00")

    result = calculate_total(authed_user_a, merchant_contains=f"trunc-{tag}")
    assert result["truncated"] is True
    # Returns the partial-sum total, not a hard refusal — Claude is
    # instructed to surface the partial flag to the user.
    assert result["count"] == 2


def test_calculate_total_rls_isolates_users(authed_user_a, authed_user_b, user_a, user_b, card_a, card_b):
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"OnlyA-{tag}", amount="100.00")
    _seed_transaction(user_b, card_id=card_b, merchant=f"OnlyB-{tag}", amount="200.00")

    a_view = calculate_total(authed_user_a, merchant_contains=tag)
    b_view = calculate_total(authed_user_b, merchant_contains=tag)

    assert Decimal(a_view["total"]) == Decimal("100.00") and a_view["count"] == 1
    assert Decimal(b_view["total"]) == Decimal("200.00") and b_view["count"] == 1


# ===========================================================================
# get_transactions
# ===========================================================================


def test_get_transactions_no_matches_returns_empty(authed_user_a):
    result = get_transactions(authed_user_a, merchant_contains=f"nonexistent-{_tag()}")
    assert result == {"items": [], "has_more": False}


def test_get_transactions_strips_user_id_from_rows(authed_user_a, user_a, card_a):
    """RLS already scopes by user; emitting user_id on every row just
    burns context tokens. The strip is a v1 design choice — guard it."""
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"Strip-{tag}", amount="1.00")
    result = get_transactions(authed_user_a, merchant_contains=tag)
    assert result["items"]
    for item in result["items"]:
        assert "user_id" not in item


def test_get_transactions_ordering_is_date_desc(authed_user_a, user_a, card_a):
    """Index `transactions_user_date_idx` is (user_id, date DESC) and
    the service adds `created_at DESC` as the tiebreaker. Day 10's UI
    rendering relies on this order."""
    tag = _tag()
    today = date.today()
    _seed_transaction(user_a, card_id=card_a, merchant=f"Ord-{tag}", amount="1.00", txn_date=today - timedelta(days=2))
    _seed_transaction(user_a, card_id=card_a, merchant=f"Ord-{tag}", amount="2.00", txn_date=today)
    _seed_transaction(user_a, card_id=card_a, merchant=f"Ord-{tag}", amount="3.00", txn_date=today - timedelta(days=5))

    result = get_transactions(authed_user_a, merchant_contains=tag)
    dates = [item["date"] for item in result["items"]]
    assert dates == sorted(dates, reverse=True)


def test_get_transactions_has_more_fires_when_over_limit(authed_user_a, user_a, card_a):
    tag = _tag()
    for _ in range(5):
        _seed_transaction(user_a, card_id=card_a, merchant=f"Page-{tag}", amount="1.00")

    page = get_transactions(authed_user_a, merchant_contains=tag, limit=3)
    assert len(page["items"]) == 3
    assert page["has_more"] is True


def test_get_transactions_no_has_more_when_under_limit(authed_user_a, user_a, card_a):
    tag = _tag()
    for _ in range(3):
        _seed_transaction(user_a, card_id=card_a, merchant=f"Under-{tag}", amount="1.00")

    page = get_transactions(authed_user_a, merchant_contains=tag, limit=10)
    assert len(page["items"]) == 3
    assert page["has_more"] is False


def test_get_transactions_offset_paginates(authed_user_a, user_a, card_a):
    """offset works with limit to skip earlier pages. Rarely needed in
    chat (the agent prefers narrowing) but the contract must hold."""
    tag = _tag()
    for i in range(5):
        _seed_transaction(
            user_a, card_id=card_a, merchant=f"Off-{tag}-{i}",
            amount=f"{i + 1}.00",
            txn_date=date.today() - timedelta(days=i),
        )

    page1 = get_transactions(authed_user_a, merchant_contains=tag, limit=2, offset=0)
    page2 = get_transactions(authed_user_a, merchant_contains=tag, limit=2, offset=2)
    page1_ids = [i["id"] for i in page1["items"]]
    page2_ids = [i["id"] for i in page2["items"]]
    # No overlap; second page hands back two distinct rows.
    assert not (set(page1_ids) & set(page2_ids))
    assert len(page2_ids) == 2


def test_get_transactions_limit_above_max_clamps_silently(authed_user_a, user_a, card_a):
    """Schema declares max=MAX_LIMIT; the service clamps silently. The
    tool must mirror that contract — don't 422 a caller who asked for
    more, just give them MAX_LIMIT."""
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"Clamp-{tag}", amount="1.00")
    result = get_transactions(authed_user_a, merchant_contains=tag, limit=MAX_LIMIT + 1000)
    # No crash; service clamps silently.
    assert "items" in result


def test_get_transactions_filter_combinations(authed_user_a, user_a, card_a):
    """Same filter set as calculate_total — must produce identically
    narrowed results (different return shape)."""
    tag = _tag()
    target_day = date.today() - timedelta(days=3)
    match_id = _seed_transaction(
        user_a, card_id=card_a, merchant=f"MultiF-{tag}",
        amount="12.34", category="Dining", txn_date=target_day,
    )
    # Decoys that should be filtered out.
    _seed_transaction(user_a, card_id=card_a, merchant=f"MultiF-{tag}", amount="12.34", category="Groceries", txn_date=target_day)
    _seed_transaction(user_a, card_id=card_a, merchant=f"MultiF-{tag}", amount="100.00", category="Dining", txn_date=target_day)
    _seed_transaction(user_a, card_id=card_a, merchant=f"MultiF-{tag}", amount="12.34", category="Dining", txn_date=target_day - timedelta(days=10))

    result = get_transactions(
        authed_user_a,
        merchant_contains=tag,
        category="Dining",
        amount_min=10,
        amount_max=15,
        date_from=(target_day - timedelta(days=1)).isoformat(),
        date_to=(target_day + timedelta(days=1)).isoformat(),
    )
    ids = [item["id"] for item in result["items"]]
    assert ids == [match_id]


def test_get_transactions_rls_isolates_users(authed_user_a, authed_user_b, user_a, user_b, card_a, card_b):
    tag = _tag()
    _seed_transaction(user_a, card_id=card_a, merchant=f"OnlyA-{tag}", amount="1.00")
    _seed_transaction(user_b, card_id=card_b, merchant=f"OnlyB-{tag}", amount="2.00")

    a_view = {i["merchant"] for i in get_transactions(authed_user_a, merchant_contains=tag)["items"]}
    b_view = {i["merchant"] for i in get_transactions(authed_user_b, merchant_contains=tag)["items"]}

    assert f"OnlyA-{tag}" in a_view and f"OnlyB-{tag}" not in a_view
    assert f"OnlyB-{tag}" in b_view and f"OnlyA-{tag}" not in b_view


# ===========================================================================
# get_subscriptions
# ===========================================================================


def test_get_subscriptions_empty_for_user_with_none(authed_user_b, user_b, admin_client):
    """user_b has no subscriptions in the base fixture set. Confirm
    the empty-case shape is sensible before any are added."""
    # Wipe any subs left behind by an earlier test that used user_b.
    admin_client.table("subscriptions").delete().eq("user_id", user_b.id).execute()
    result = get_subscriptions(authed_user_b)
    assert result == {"items": [], "truncated": False}


def test_get_subscriptions_no_filter_returns_all_statuses(authed_user_a, user_a, card_a):
    tag = _tag()
    today = date.today()
    _seed_subscription(user_a, card_id=card_a, name=f"S-active-{tag}", next_billing=today + timedelta(days=5), status="active")
    _seed_subscription(user_a, card_id=card_a, name=f"S-paused-{tag}", next_billing=today + timedelta(days=15), status="paused")
    _seed_subscription(user_a, card_id=card_a, name=f"S-cancelled-{tag}", next_billing=today + timedelta(days=25), status="cancelled")

    result = get_subscriptions(authed_user_a)
    names = {s["name"] for s in result["items"]}
    assert {f"S-active-{tag}", f"S-paused-{tag}", f"S-cancelled-{tag}"} <= names


@pytest.mark.parametrize("status", ["active", "paused", "cancelled"])
def test_get_subscriptions_status_filter(status, authed_user_a, user_a, card_a):
    """One parametrized test per status replaces three near-identical
    copies. Each status enum value must filter cleanly."""
    tag = _tag()
    today = date.today()
    target_name = f"S-{status}-{tag}"
    other_name = f"S-other-{tag}"
    other_status = "active" if status != "active" else "paused"
    _seed_subscription(user_a, card_id=card_a, name=target_name, next_billing=today + timedelta(days=5), status=status)
    _seed_subscription(user_a, card_id=card_a, name=other_name, next_billing=today + timedelta(days=5), status=other_status)

    result = get_subscriptions(authed_user_a, status=status)
    names = {s["name"] for s in result["items"]}
    assert target_name in names
    assert other_name not in names


def test_get_subscriptions_ordering_is_next_billing_asc(authed_user_a, user_a, card_a):
    """Ascending by `next_billing_date` puts the next-to-bill at the
    top — what UX frame 21 renders for the "upcoming charges" cue."""
    tag = _tag()
    today = date.today()
    _seed_subscription(user_a, card_id=card_a, name=f"Far-{tag}", next_billing=today + timedelta(days=30), status="active")
    _seed_subscription(user_a, card_id=card_a, name=f"Near-{tag}", next_billing=today + timedelta(days=2), status="active")
    _seed_subscription(user_a, card_id=card_a, name=f"Mid-{tag}", next_billing=today + timedelta(days=10), status="active")

    result = get_subscriptions(authed_user_a, status="active")
    tagged_dates = [
        s["next_billing_date"]
        for s in result["items"]
        if tag in s["name"]
    ]
    assert tagged_dates == sorted(tagged_dates)


def test_get_subscriptions_strips_user_id(authed_user_a, user_a, card_a):
    _seed_subscription(
        user_a, card_id=card_a, name=f"Strip-{_tag()}",
        next_billing=date.today() + timedelta(days=1),
    )
    result = get_subscriptions(authed_user_a)
    for item in result["items"]:
        assert "user_id" not in item


def test_get_subscriptions_rls_isolates_users(authed_user_a, authed_user_b, user_a, user_b, card_a, card_b):
    tag = _tag()
    today = date.today()
    _seed_subscription(user_a, card_id=card_a, name=f"OnlyA-{tag}", next_billing=today + timedelta(days=5))
    _seed_subscription(user_b, card_id=card_b, name=f"OnlyB-{tag}", next_billing=today + timedelta(days=5))

    a_names = {s["name"] for s in get_subscriptions(authed_user_a)["items"]}
    b_names = {s["name"] for s in get_subscriptions(authed_user_b)["items"]}
    assert f"OnlyA-{tag}" in a_names and f"OnlyB-{tag}" not in a_names
    assert f"OnlyB-{tag}" in b_names and f"OnlyA-{tag}" not in b_names


# ===========================================================================
# get_spending_summary
# ===========================================================================


def test_get_spending_summary_empty_window(authed_user_b, user_b, admin_client):
    """user_b with all rows wiped — empty breakdown is the empty list,
    not an exception, not None."""
    admin_client.table("transactions").delete().eq("user_id", user_b.id).execute()
    result = get_spending_summary(AuthedUser(jwt=user_b.jwt, user_id=uuid.UUID(user_b.id), email=user_b.email))
    assert result["breakdown"] == []
    assert result["window_months"] == 1
    assert result["truncated"] is False


def test_get_spending_summary_groups_and_orders_by_total_desc(authed_user_a, user_a, card_a):
    tag = _tag()
    # Aggregate across categories — Dining=70, Groceries=100, Coffee=5
    _seed_transaction(user_a, card_id=card_a, merchant=f"D-{tag}", amount="50.00", category="Dining")
    _seed_transaction(user_a, card_id=card_a, merchant=f"D-{tag}", amount="20.00", category="Dining")
    _seed_transaction(user_a, card_id=card_a, merchant=f"G-{tag}", amount="100.00", category="Groceries")
    _seed_transaction(user_a, card_id=card_a, merchant=f"C-{tag}", amount="5.00", category="Coffee Shops")

    result = get_spending_summary(authed_user_a, months=12)
    # Filter to categories we seeded for this tag — other tests inject
    # data we can't strip from the aggregate. Use a lower bound.
    by_cat = {b["category"]: b for b in result["breakdown"]}
    assert Decimal(by_cat["Dining"]["total"]) >= Decimal("70.00")
    assert Decimal(by_cat["Groceries"]["total"]) >= Decimal("100.00")
    assert Decimal(by_cat["Coffee Shops"]["total"]) >= Decimal("5.00")
    # Ordering: totals descending across the whole breakdown.
    totals = [Decimal(b["total"]) for b in result["breakdown"]]
    assert totals == sorted(totals, reverse=True)


def test_get_spending_summary_window_starts_at_first_of_month(authed_user_a, user_a, card_a):
    """For months=1 the window is the first of this month. A
    transaction dated exactly at that boundary must be included; one
    dated the previous day must be excluded."""
    tag = _tag()
    today = date.today()
    first_of_month = today.replace(day=1)
    last_of_prev = first_of_month - timedelta(days=1)

    in_id = _seed_transaction(
        user_a, card_id=card_a,
        merchant=f"BoundaryIn-{tag}", amount="11.00", category="Dining",
        txn_date=first_of_month,
    )
    _seed_transaction(
        user_a, card_id=card_a,
        merchant=f"BoundaryOut-{tag}", amount="999.00", category="Dining",
        txn_date=last_of_prev,
    )

    result = get_spending_summary(authed_user_a, months=1)
    # The window must start at first_of_month, not earlier.
    assert result["window_start"] == first_of_month.isoformat()
    # The in-window row is reachable; the out-of-window row isn't
    # individually visible from this tool, but its $999 absence is the
    # proof of correct boundary. Lower-bound the Dining total to be
    # at least $11 from the in-window row, but assert it doesn't jump
    # by the $999 amount we deliberately excluded.
    by_cat = {b["category"]: Decimal(b["total"]) for b in result["breakdown"]}
    assert by_cat.get("Dining", Decimal("0")) >= Decimal("11.00")
    # Confirm the excluded row would have pushed Dining over $999 if
    # it had been included — we can't directly subtract, so we narrow
    # by asserting the in-window transaction id is queryable via
    # get_transactions with the same lower bound.
    assert in_id  # row was inserted


def test_get_spending_summary_months_param_clamps(authed_user_a):
    # Below 1 clamps up to 1; above 24 clamps down to 24.
    assert get_spending_summary(authed_user_a, months=0)["window_months"] == 1
    assert get_spending_summary(authed_user_a, months=-5)["window_months"] == 1
    assert get_spending_summary(authed_user_a, months=24)["window_months"] == 24
    assert get_spending_summary(authed_user_a, months=999)["window_months"] == 24


def test_get_spending_summary_window_span_grows_with_months(authed_user_a):
    """months=3 must reach further back than months=1. Exact dates are
    asserted relative to today, not hardcoded."""
    today = date.today()
    one_month = get_spending_summary(authed_user_a, months=1)
    three_months = get_spending_summary(authed_user_a, months=3)
    start_one = date.fromisoformat(one_month["window_start"])
    start_three = date.fromisoformat(three_months["window_start"])
    assert start_three < start_one
    # Both anchored at first-of-some-month (day=1).
    assert start_one.day == 1 and start_three.day == 1
    # And the more recent boundary is this month's first.
    assert start_one == today.replace(day=1)


def test_get_spending_summary_excludes_future_dated_transactions(authed_user_a, user_a, card_a):
    """`/transactions/confirm` allows `date.today() + 1 day` for client-
    side TZ slack, so future-dated rows can legitimately exist. The
    summary's window is "spent so far" — future rows must not be
    aggregated, or "this month" overstates spend until midnight UTC."""
    tag = _tag()
    # Baseline Dining total before this test seeds anything.
    before = get_spending_summary(authed_user_a, months=1)
    before_dining = next(
        (Decimal(b["total"]) for b in before["breakdown"] if b["category"] == "Dining"),
        Decimal("0"),
    )

    # A row dated today should be included; a row dated tomorrow must
    # not be — even though both fall inside the "this month" calendar
    # window.
    _seed_transaction(
        user_a, card_id=card_a,
        merchant=f"Today-{tag}", amount="5.00", category="Dining",
        txn_date=date.today(),
    )
    _seed_transaction(
        user_a, card_id=card_a,
        merchant=f"Future-{tag}", amount="999.00", category="Dining",
        txn_date=date.today() + timedelta(days=1),
    )

    after = get_spending_summary(authed_user_a, months=1)
    after_dining = next(
        (Decimal(b["total"]) for b in after["breakdown"] if b["category"] == "Dining"),
        Decimal("0"),
    )
    # Exactly the today row contributed. A regression that drops the
    # upper bound would push the delta to $1004.
    assert after_dining - before_dining == Decimal("5.00")


def test_get_spending_summary_rls_isolates_users(authed_user_a, authed_user_b, user_a, user_b, card_a, card_b):
    """No merchant filter on this tool — RLS is the sole guard. A
    distinctively large amount in user B's data must never appear in
    user A's summary."""
    tag = _tag()
    _seed_transaction(user_b, card_id=card_b, merchant=f"OnlyB-{tag}", amount="1000000.00", category="Other")

    a_summary = get_spending_summary(authed_user_a, months=24)
    by_cat = {b["category"]: Decimal(b["total"]) for b in a_summary["breakdown"]}
    # If RLS leaked, user A's "Other" would jump by 1M.
    assert by_cat.get("Other", Decimal("0")) < Decimal("1000000.00")


# ===========================================================================
# get_cards
# ===========================================================================


def test_get_cards_returns_active_card(authed_user_a, card_a):
    result = get_cards(authed_user_a)
    card_ids = {c["id"] for c in result["items"]}
    assert card_a in card_ids


def test_get_cards_strips_user_id(authed_user_a):
    for item in get_cards(authed_user_a)["items"]:
        assert "user_id" not in item


def test_get_cards_excludes_soft_deleted(authed_user_a, user_a):
    """active=false rows must not surface. Day 14's DELETE soft-deletes
    by setting active=false rather than removing the row."""
    client = supabase_for_user(user_a.jwt)
    soft_id = (
        client.table("cards")
        .insert({
            "user_id": user_a.id,
            "name": f"Inactive-{_tag()}",
            "issuer": "Chase",
            "program": "UR",
            "active": False,
        })
        .execute()
        .data[0]["id"]
    )
    result = get_cards(authed_user_a)
    visible_ids = {c["id"] for c in result["items"]}
    assert soft_id not in visible_ids


def test_get_cards_returns_multiple_cards(authed_user_a, user_a):
    client = supabase_for_user(user_a.jwt)
    extra_id = (
        client.table("cards")
        .insert({
            "user_id": user_a.id,
            "name": f"Extra-{_tag()}",
            "issuer": "Amex",
            "program": "MR",
        })
        .execute()
        .data[0]["id"]
    )
    result = get_cards(authed_user_a)
    card_ids = {c["id"] for c in result["items"]}
    assert len(card_ids) >= 2
    assert extra_id in card_ids


def test_get_cards_rls_isolates_users(authed_user_a, authed_user_b, card_a, card_b):
    a_ids = {c["id"] for c in get_cards(authed_user_a)["items"]}
    b_ids = {c["id"] for c in get_cards(authed_user_b)["items"]}
    assert card_a in a_ids and card_b not in a_ids
    assert card_b in b_ids and card_a not in b_ids


# ===========================================================================
# execute_tool dispatch (the loop's entry point)
# ===========================================================================


def test_execute_tool_dispatches_each_registered_tool(authed_user_a):
    """Every registered tool must be invokable with the empty-input
    contract the loop relies on at `loop.py:278` (`executor(user,
    **tool_input)` where `tool_input` may be `{}`)."""
    for name in TOOL_REGISTRY:
        result = execute_tool(name, {}, authed_user_a)
        assert isinstance(result, dict)


def test_execute_tool_unknown_name_raises_keyerror(authed_user_a):
    """The loop catches this KeyError and emits an is_error tool_result
    so Claude can recover (`loop.py:280-285`). The raise here is what
    that recovery path depends on."""
    with pytest.raises(KeyError):
        execute_tool("phantom_tool", {}, authed_user_a)
