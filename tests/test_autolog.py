"""Day 19 — pg_cron subscription auto-logger contract suite.

Exercises `autolog_subscriptions()` via PostgREST RPC with the service
role. The function lives in migration
`20260518130200_subscription_autolog_function.sql`, is SECURITY DEFINER,
and is callable only by `service_role` (REVOKE FROM PUBLIC + GRANT TO
service_role). The `admin_client` fixture from conftest.py is the
service-role caller.

Covers:

- Forward-only auto-log on a due cardful subscription — one transaction
  inserted, `next_billing_date` advanced, `source='auto_logged'`.
- Cardless ACH subscription auto-logs with `card_id=NULL`.
- Re-running the cron on the same day is idempotent (zero new rows
  even after rolling `next_billing_date` back).
- A `status='paused'` subscription is not auto-logged.
- A `status='cancelled'` subscription is not auto-logged.

Advisory-lock isolation (`pg_try_advisory_lock(8830731)` returns false
when held by another connection) is verified by code review of the SQL
function rather than tested here — building a cross-process test would
need a second open Postgres connection (psycopg) outside the project's
dependency set. The lock primitive itself is a Postgres builtin and
well-understood; the value-to-effort ratio matches the
`test_memory_cleanup.py` precedent for the equivalent prune_user_memory
function.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest

# Teardown for the subscriptions/transactions this module seeds on the shared user_a (audit P3-38).
pytestmark = pytest.mark.usefixtures("cleanup_user_a_ledger")



def test_autolog_due_cardful_subscription(admin_client, user_a, card_a):
    """Verify a due cardful subscription auto-logs one row with the right shape.

    Seeds a subscription with `next_billing_date = today` and exercises
    the autolog function. Asserts a single `auto_logged` transaction is
    produced, attribution matches the source subscription, and
    `next_billing_date` advances one month.
    """
    sub_id = _seed_subscription(
        admin_client,
        user_id=user_a.id,
        card_id=card_a,
        name=f"Auto-{uuid.uuid4().hex[:6]}",
        frequency="monthly",
        amount="9.99",
        next_billing_date=date.today() - timedelta(days=1),
    )

    admin_client.rpc("autolog_subscriptions", {}).execute()

    rows = _fetch_transactions_for_subscription(admin_client, sub_id)
    assert len(rows) == 1, f"exactly one auto-logged tx expected; got {rows}"
    tx = rows[0]
    assert tx["source"] == "auto_logged"
    assert tx["card_id"] == card_a
    assert Decimal(str(tx["amount"])) == Decimal("9.99")

    sub = _fetch_subscription(admin_client, sub_id)
    expected = _add_month(date.today() - timedelta(days=1))
    assert sub["next_billing_date"] == expected.isoformat()


def test_autolog_cardless_subscription(admin_client, user_a):
    """A subscription with `card_id=NULL` auto-logs with `card_id=NULL`.

    DESIGN.md §8.3 — bank-ACH bills (rent, utilities) are first-class.
    pg_cron passes `card_id` through verbatim to the new transaction.
    """
    sub_id = _seed_subscription(
        admin_client,
        user_id=user_a.id,
        card_id=None,
        name=f"Rent-{uuid.uuid4().hex[:6]}",
        frequency="monthly",
        amount="2400.00",
        next_billing_date=date.today(),
    )

    admin_client.rpc("autolog_subscriptions", {}).execute()

    rows = _fetch_transactions_for_subscription(admin_client, sub_id)
    assert len(rows) == 1
    assert rows[0]["card_id"] is None
    assert rows[0]["source"] == "auto_logged"


def test_autolog_idempotent_on_same_day(admin_client, user_a, card_a):
    """Re-running the cron on the same day inserts zero rows.

    Idempotency comes from the partial unique index on
    `transactions (subscription_id, date) WHERE status='active' AND
    subscription_id IS NOT NULL` (migration 20260516150000). The
    function's `ON CONFLICT` predicate matches that exactly — the test
    confirms.
    """
    sub_id = _seed_subscription(
        admin_client,
        user_id=user_a.id,
        card_id=card_a,
        name=f"Idem-{uuid.uuid4().hex[:6]}",
        frequency="monthly",
        amount="5.00",
        next_billing_date=date.today(),
    )

    admin_client.rpc("autolog_subscriptions", {}).execute()
    rows_first = _fetch_transactions_for_subscription(admin_client, sub_id)
    assert len(rows_first) == 1

    # Roll next_billing_date back to today so the function re-tries the
    # insert; without the partial unique index it would produce a dup.
    admin_client.table("subscriptions").update(
        {"next_billing_date": date.today().isoformat()}
    ).eq("id", sub_id).execute()
    admin_client.rpc("autolog_subscriptions", {}).execute()
    rows_second = _fetch_transactions_for_subscription(admin_client, sub_id)
    assert len(rows_second) == 1, "idempotency: still exactly one row"


def test_autolog_advance_restores_start_date_anchor_day(admin_client, user_a, card_a):
    """A short-month clamp must not permanently drift the billing day.

    A "monthly on the 31st" subscription whose next_billing_date was
    clamped to Feb 28 must advance to Mar *31*, not Mar 28 — the advance
    is anchored to start_date's day-of-month (LEAST(anchor, month length)),
    not compounded from the previously clamped date. Before the
    20260610120100 migration this drifted permanently (Feb 28 → Mar 28 →
    Apr 28 …) with no user-side repair, since frequency/start_date are
    immutable post-create.
    """
    # Last year's Jan 31 so the dates are due (<= today) on any run date.
    anchor_start = date(date.today().year - 1, 1, 31)
    clamped_feb = _add_month(anchor_start)  # Feb 28 (or 29) — the clamp
    assert clamped_feb.day < 31
    sub_id = _seed_subscription(
        admin_client,
        user_id=user_a.id,
        card_id=card_a,
        name=f"Anchor-{uuid.uuid4().hex[:6]}",
        frequency="monthly",
        amount="14.99",
        next_billing_date=clamped_feb,
        start_date=anchor_start,
    )

    admin_client.rpc("autolog_subscriptions", {}).execute()

    sub = _fetch_subscription(admin_client, sub_id)
    assert sub["next_billing_date"] == date(
        anchor_start.year, 3, 31
    ).isoformat(), "advance must restore the day-31 anchor after a short month"


def test_autolog_skips_paused_subscriptions(admin_client, user_a, card_a):
    """A `status='paused'` subscription is not auto-logged.

    This is what makes the §8.3 split-cascade rule safe — when a card
    soft-delete flips regular subscriptions to paused, the cron stops
    firing on them until the user reassigns and resumes.
    """
    sub_id = _seed_subscription(
        admin_client,
        user_id=user_a.id,
        card_id=card_a,
        name=f"Paused-{uuid.uuid4().hex[:6]}",
        frequency="monthly",
        amount="5.00",
        next_billing_date=date.today(),
        status="paused",
    )

    admin_client.rpc("autolog_subscriptions", {}).execute()
    rows = _fetch_transactions_for_subscription(admin_client, sub_id)
    assert len(rows) == 0


def test_autolog_skips_cancelled_subscriptions(admin_client, user_a, card_a):
    """A `status='cancelled'` subscription is not auto-logged."""
    sub_id = _seed_subscription(
        admin_client,
        user_id=user_a.id,
        card_id=card_a,
        name=f"Cancelled-{uuid.uuid4().hex[:6]}",
        frequency="monthly",
        amount="5.00",
        next_billing_date=date.today(),
        status="cancelled",
    )

    admin_client.rpc("autolog_subscriptions", {}).execute()
    rows = _fetch_transactions_for_subscription(admin_client, sub_id)
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _seed_subscription(
    admin_client,
    *,
    user_id: str,
    card_id: str | None,
    name: str,
    frequency: str,
    amount: str,
    next_billing_date: date,
    status: str = "active",
    start_date: date | None = None,
) -> str:
    """Insert a subscriptions row via the service-role admin client.

    Bypasses RLS so tests can seed under any user_id without going
    through the chat propose/confirm path. The cron function itself
    runs under SECURITY DEFINER so the seeding role doesn't matter for
    the assertions below. `start_date` defaults to `next_billing_date`;
    pass it explicitly to exercise the anchor-day advance.
    """
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "card_id": card_id,
        "name": name,
        "amount": amount,
        "frequency": frequency,
        "start_date": (start_date or next_billing_date).isoformat(),
        "next_billing_date": next_billing_date.isoformat(),
        "category": "Memberships",
        "status": status,
    }
    admin_client.table("subscriptions").insert(row).execute()
    return row["id"]


def _fetch_transactions_for_subscription(admin_client, sub_id: str) -> list[dict]:
    """Return active transactions linked to this subscription."""
    resp = (
        admin_client.table("transactions")
        .select("id, subscription_id, card_id, source, amount, date")
        .eq("subscription_id", sub_id)
        .eq("status", "active")
        .execute()
    )
    return list(resp.data or [])


def _fetch_subscription(admin_client, sub_id: str) -> dict:
    """Return the subscription row by id."""
    resp = (
        admin_client.table("subscriptions")
        .select("next_billing_date, status")
        .eq("id", sub_id)
        .single()
        .execute()
    )
    return resp.data


def _add_month(d: date) -> date:
    """Return d + 1 month, clamping to month-end where needed."""
    total = d.month + 1
    year = d.year + (total - 1) // 12
    month = (total - 1) % 12 + 1
    import calendar

    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))
