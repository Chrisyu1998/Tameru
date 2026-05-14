"""Day 9c — render_user_merchants integration tests.

Real local Supabase + real RLS so the `top_user_merchants` view's
`security_invoker = true` is exercised (mock client would mask a missing
option and the test would pass against broken DDL). Each test tags its
seeded merchants with a `uuid4().hex[:8]` suffix so session-scoped
fixture data accumulating across the suite never collides with the
assertion-narrowed subset.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from supabase import create_client

from app.db import supabase_for_user
from app.prompts.chat import render_user_merchants
from tests.conftest import TestUser, _delete_user, _make_user


# ---------------------------------------------------------------------------
# Function-scoped fresh-user fixtures.
#
# Session-scoped user_a / user_b accumulate hundreds of single-visit merchants
# across the wider suite (notably test_tools.py). The view caps results at 30,
# so single-visit test merchants seeded here would silently fall off the
# bottom and the ordering / RLS-isolation assertions would fail under full
# pytest runs even though they pass in isolation. A fresh user per test gives
# us a deterministic merchant universe.
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_user_a(admin_client, supabase_env) -> TestUser:
    """Yield a freshly-created user with no transaction history."""
    user = _make_user(
        admin_client,
        supabase_env["url"],
        supabase_env["anon_key"],
        f"merch-a-{uuid.uuid4().hex[:6]}",
    )
    yield user
    _delete_user(admin_client, user.id)


@pytest.fixture
def fresh_user_b(admin_client, supabase_env) -> TestUser:
    """Yield a second freshly-created user for two-user RLS isolation tests."""
    user = _make_user(
        admin_client,
        supabase_env["url"],
        supabase_env["anon_key"],
        f"merch-b-{uuid.uuid4().hex[:6]}",
    )
    yield user
    _delete_user(admin_client, user.id)


# ---------------------------------------------------------------------------
# Populated case: the block lists the user's top merchants and frames the
# canonicalization instruction.
# ---------------------------------------------------------------------------


def test_render_lists_seeded_merchant(fresh_user_a):
    """Seed five visits to a canonical merchant; the block must mention it
    by exact name and include the framing line that teaches Claude to
    canonicalize variants ("KFC" → "Kentucky Fried Chicken"). Without the
    framing line, the prompt block degenerates into trivia."""
    tag = _tag()
    merchant = f"Kentucky Fried Chicken {tag}"
    for _ in range(5):
        _seed_transaction(fresh_user_a, card_id=None, merchant=merchant, amount="12.00")

    block = render_user_merchants(fresh_user_a.jwt)

    assert merchant in block
    assert "When the user mentions a merchant" in block
    assert "propose_transaction" in block


def test_frequency_orders_above_single_visit(fresh_user_a):
    """The view orders by COUNT(*) DESC. A 5-visit merchant must appear
    before a 1-visit merchant in the rendered block — that's how Claude
    picks the right canonical when two merchants both partially match
    the user's typed string."""
    tag = _tag()
    frequent = f"Frequent {tag}"
    rare = f"Rare {tag}"
    for _ in range(5):
        _seed_transaction(fresh_user_a, card_id=None, merchant=frequent, amount="10.00")
    _seed_transaction(fresh_user_a, card_id=None, merchant=rare, amount="10.00")

    block = render_user_merchants(fresh_user_a.jwt)

    assert block.index(frequent) < block.index(rare)


def test_recency_breaks_frequency_tie(fresh_user_a):
    """When two merchants share visit count, the more recently seen one
    ranks higher (view ORDER BY ... MAX(date) DESC). This is the second
    tie-breaker; it matters when a returning user has stale popular
    merchants competing with a current favorite."""
    tag = _tag()
    older = f"Older {tag}"
    newer = f"Newer {tag}"
    today = date.today()
    # Two visits each so frequency is tied.
    for _ in range(2):
        _seed_transaction(
            fresh_user_a, card_id=None, merchant=older,
            amount="10.00", txn_date=today - timedelta(days=20),
        )
        _seed_transaction(
            fresh_user_a, card_id=None, merchant=newer,
            amount="10.00", txn_date=today - timedelta(days=2),
        )

    block = render_user_merchants(fresh_user_a.jwt)

    assert block.index(newer) < block.index(older)


# ---------------------------------------------------------------------------
# Empty case: the block is always present, never an empty string.
# ---------------------------------------------------------------------------


def test_empty_user_returns_placeholder_block(user_unbootstrapped, admin_client, supabase_env):
    """A brand-new user with no transactions still gets a block. If this
    returned an empty string, render_system_prompt's two-block array
    would have a zero-length block[1] and any downstream "is the
    merchants block populated" check would get confused. The placeholder
    keeps the array shape stable."""
    # user_unbootstrapped has no users_meta row; sign-in worked but no
    # transactions exist. We use it directly here because the
    # session-scoped user_a / user_b accumulate transactions from other
    # tests in the suite.
    block = render_user_merchants(user_unbootstrapped.jwt)
    assert block == "(No prior merchants yet.)"


# ---------------------------------------------------------------------------
# RLS isolation: user B's merchants never leak into user A's block. This
# is the test that catches a missing `WITH (security_invoker = true)` on
# the view — without that option, the view runs as its owner (effectively
# bypassing RLS) and returns every user's merchants.
# ---------------------------------------------------------------------------


def test_rls_isolates_users(fresh_user_a, fresh_user_b):
    """Seed user_a-only and user_b-only merchants with unique tags; each
    user's block must contain only their own. The view's
    `security_invoker = true` is what makes RLS fire on the underlying
    transactions table — without that option, every user's merchants
    would leak into every block. This test is the catch-net for a
    missing or removed option on the view DDL."""
    tag_a = f"a-{_tag()}"
    tag_b = f"b-{_tag()}"
    merchant_a = f"OnlyForA {tag_a}"
    merchant_b = f"OnlyForB {tag_b}"
    _seed_transaction(fresh_user_a, card_id=None, merchant=merchant_a, amount="10.00")
    _seed_transaction(fresh_user_b, card_id=None, merchant=merchant_b, amount="10.00")

    block_a = render_user_merchants(fresh_user_a.jwt)
    block_b = render_user_merchants(fresh_user_b.jwt)

    assert merchant_a in block_a
    assert merchant_a not in block_b
    assert merchant_b in block_b
    assert merchant_b not in block_a


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _tag() -> str:
    """Short unique tag to namespace seeded merchants per test."""
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
    """Insert one transaction via the user's RLS-scoped client; return id.

    Duplicated rather than imported from tests/test_tools.py because the
    helper there is module-private; lifting it to a shared fixture file
    is a wider refactor than this day asks for.
    """
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
