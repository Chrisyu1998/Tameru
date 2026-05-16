"""Integration tests for GET /dashboard/summary — Day 13.

Exercises the full chain: HTTP handler → `compute_dashboard_summary` →
`dashboard_summary(p_today)` Postgres function → soft new-user gate →
typed `DashboardSummary` response.

Tests seed real rows into the local Supabase stack via the user's JWT
so the RLS-scoped RPC fires exactly as it will in production.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provide a fresh TestClient for the FastAPI app under test."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Empty-history user
# ---------------------------------------------------------------------------


def test_summary_zero_history_returns_empty_state(client, user_b):
    """User with no transactions sees baseline_ready=false + keep-logging copy.

    user_b is the second session-scoped user; in test_transactions.py it
    only appears in RLS cross-tenant checks (never gets transactions
    seeded), so it's the cleanest ledger we have to assert the empty
    state against. We clear defensively in case test ordering shifts.
    """
    _clear_transactions(user_b)

    resp = client.get("/dashboard/summary", headers=_auth(user_b))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["baseline_ready"] is False
    assert body["categories"] == []
    assert body["observation"] is not None
    assert "keep logging" in body["observation"].lower()


# ---------------------------------------------------------------------------
# Soft new-user gate
# ---------------------------------------------------------------------------


def test_summary_per_category_baseline_ready_flips_with_history(
    client, user_a, card_a
):
    """Categories with ≥6 prior tx AND ≥30 days history get a real baseline; others don't.

    Seeds two categories:
      - Dining: 10 transactions spread over the last 4 months → gate clears.
      - Coffee Shops: 3 transactions in the last week → gate stays closed.
    """
    _clear_transactions(user_a)

    today = date.today()
    # Dining: a row each in the four prior months + a few this month —
    # comfortably above both gate thresholds.
    for delta_months in (3, 2, 1):
        anchor = (today.replace(day=1) - timedelta(days=1)).replace(day=15)
        for offset in range(3):
            _seed_transaction(
                user_a,
                card_a,
                merchant=f"Dining-{_tag()}",
                amount=Decimal("20"),
                category="Dining",
                txn_date=anchor - timedelta(days=delta_months * 30 + offset),
            )
    # Coffee Shops: only this week.
    for offset in range(3):
        _seed_transaction(
            user_a,
            card_a,
            merchant=f"Cafe-{_tag()}",
            amount=Decimal("5"),
            category="Coffee Shops",
            txn_date=today - timedelta(days=offset),
        )

    resp = client.get("/dashboard/summary", headers=_auth(user_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    by_name = {tile["name"]: tile for tile in body["categories"]}
    assert "Dining" in by_name
    dining = by_name["Dining"]
    assert dining["baseline_ready"] is True
    assert dining["baseline"] is not None

    if "Coffee Shops" in by_name:
        coffee = by_name["Coffee Shops"]
        assert coffee["baseline_ready"] is False
        assert coffee["baseline"] is None
        assert coffee["delta_pct"] is None


# ---------------------------------------------------------------------------
# Historic-only category renders -baseline delta
# ---------------------------------------------------------------------------


def test_summary_historic_only_category_keeps_negative_delta(
    client, user_a, card_a
):
    """Category with prior baseline but zero this-month spend renders delta=-baseline."""
    _clear_transactions(user_a)

    today = date.today()
    # Three prior months of Groceries spend; nothing this month.
    for delta_months in (3, 2, 1):
        first_of_prior_month = (today.replace(day=1) - timedelta(days=delta_months * 30))
        for offset in range(3):
            _seed_transaction(
                user_a,
                card_a,
                merchant=f"Groc-{_tag()}",
                amount=Decimal("40"),
                category="Groceries",
                txn_date=first_of_prior_month + timedelta(days=offset),
            )

    resp = client.get("/dashboard/summary", headers=_auth(user_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_name = {tile["name"]: tile for tile in body["categories"]}
    assert "Groceries" in by_name
    groc = by_name["Groceries"]
    assert groc["this_month"] in ("0", "0.00", 0)
    assert groc["baseline_ready"] is True
    # delta_abs equals -baseline (since this_month is 0).
    assert Decimal(str(groc["delta_abs"])) == -Decimal(str(groc["baseline"]))


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Build the Bearer + X-Device-Id headers Day 7's device gate requires."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _tag() -> str:
    """Short unique suffix so seeded merchants don't collide across tests."""
    return uuid.uuid4().hex[:8]


def _seed_transaction(
    user,
    card_id: str,
    *,
    merchant: str,
    amount: Decimal,
    category: str,
    txn_date: date,
) -> None:
    """Insert one row via the user's RLS-scoped client (no API round trip)."""
    client = supabase_for_user(user.jwt)
    client.table("transactions").insert(
        {
            "user_id": user.id,
            "card_id": card_id,
            "merchant": merchant,
            "amount": str(amount),
            "date": txn_date.isoformat(),
            "category": category,
            "source": "manual",
        }
    ).execute()


def _clear_transactions(user) -> None:
    """Wipe the user's transactions so each test starts from a known state."""
    client = supabase_for_user(user.jwt)
    client.table("transactions").delete().eq("user_id", user.id).execute()
