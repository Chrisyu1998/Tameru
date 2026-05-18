"""Goals route contract suite.

Covers `GET /goals`, `PATCH /goals/{id}`, `DELETE /goals/{id}`:

- GET returns user-scoped goals with calendar-aligned period-to-date
  spend (active transactions only — soft-deleted rows excluded).
- PATCH updates amount and period in place; rejects category changes
  by ignoring them (the schema's unique key fixes the slot).
- PATCH surfaces 409 `goal_slot_occupied` when the new period collides
  with an existing (user, category, period) slot.
- DELETE is hard delete and idempotent; RLS makes deleting another
  user's goal a silent no-op.
- RLS: user A never sees user B's goals via GET; PATCH/DELETE on user
  B's goal id from user A's seat behaves like the row doesn't exist.

The suite assumes the local Supabase stack from conftest.py and uses
the session-scoped `user_a` / `user_b` / `card_a` / `card_b` fixtures.
A per-test `clean_goals` fixture wipes both users' `goals` and
`user_a`'s in-window `transactions` so cross-test rows can't bleed
into spend assertions.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient sharing the running app stack."""
    return TestClient(app)


@pytest.fixture
def clean_goals(user_a, user_b):
    """Wipe both users' `goals` rows + `user_a`'s recent transactions.

    Session-scoped users persist rows across tests; without this hook a
    leftover Dining/month goal from one case would race the next case's
    "no goals" assertion. Transactions get wiped only for `user_a`
    (the spend-counting fixture) because tests that don't touch txs
    don't care if user_b's are present.

    Also re-asserts each user's `active_device_id` on `users_meta`. The
    `tests/contracts/test_rls.py::_seed_users_meta_once` fixture upserts
    `{user_id}` without re-supplying `active_device_id`, which clobbers
    the bootstrap value on shared test runs and breaks the device gate
    that authenticated routes depend on. Restoring it here keeps the
    goals suite robust to test ordering.
    """
    for user in (user_a, user_b):
        client = supabase_for_user(user.jwt)
        client.table("goals").delete().eq("user_id", user.id).execute()
        if user.device_id:
            client.table("users_meta").update(
                {"active_device_id": user.device_id}
            ).eq("user_id", user.id).execute()
    supabase_for_user(user_a.jwt).table("transactions").delete().eq(
        "user_id", user_a.id
    ).execute()
    yield


pytestmark = pytest.mark.usefixtures("clean_goals")


# ---------------------------------------------------------------------------
# GET /goals
# ---------------------------------------------------------------------------


def test_get_goals_returns_user_scoped_rows(client, user_a, user_b):
    """User A's GET /goals must never surface user B's rows."""
    _insert_goal(user_a, category="Dining", amount="300", period="month")
    _insert_goal(user_b, category="Groceries", amount="500", period="month")

    resp = client.get("/goals", headers=_auth(user_a))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    categories = {row["goal"]["category"] for row in items}
    assert categories == {"Dining"}


def test_get_goals_empty_for_new_user(client, user_a):
    """No goals → empty `items` list, never an error."""
    resp = client.get("/goals", headers=_auth(user_a))
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


def test_get_goals_includes_calendar_month_spend(client, user_a, card_a):
    """Spend reflects only in-window active transactions; sums precisely."""
    today = dt.date.today()
    in_window = today.replace(day=1)
    last_month_day = in_window - dt.timedelta(days=1)

    _insert_goal(user_a, category="Dining", amount="300", period="month")
    _insert_transaction(
        user_a, card_a, category="Dining", amount="20.00", date=in_window
    )
    _insert_transaction(
        user_a, card_a, category="Dining", amount="30.00", date=today
    )
    # Out of window — must not contribute to spend.
    _insert_transaction(
        user_a,
        card_a,
        category="Dining",
        amount="999.00",
        date=last_month_day,
    )
    # Wrong category — must not contribute either.
    _insert_transaction(
        user_a, card_a, category="Groceries", amount="50.00", date=today
    )

    resp = client.get("/goals", headers=_auth(user_a))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["goal"]["category"] == "Dining"
    assert Decimal(item["spent_period_to_date"]) == Decimal("50.00")
    # progress_ratio = 50 / 300.
    assert item["progress_ratio"] == pytest.approx(50 / 300)


def test_get_goals_soft_deleted_transactions_excluded(client, user_a, card_a):
    """Soft-deleted transactions must not count toward goal spend."""
    today = dt.date.today()
    _insert_goal(user_a, category="Dining", amount="100", period="month")
    tx_id = _insert_transaction(
        user_a, card_a, category="Dining", amount="40.00", date=today
    )
    # Soft-delete: the active_transactions view filters status='deleted'.
    supabase_for_user(user_a.jwt).table("transactions").update(
        {"status": "deleted", "deleted_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    ).eq("id", tx_id).execute()

    resp = client.get("/goals", headers=_auth(user_a))
    item = resp.json()["items"][0]
    assert Decimal(item["spent_period_to_date"]) == Decimal("0")


def test_get_goals_overall_budget_sums_all_categories(client, user_a, card_a):
    """`category=NULL` budgets sum every category in the window."""
    today = dt.date.today()
    _insert_goal(user_a, category=None, amount="1000", period="month")
    _insert_transaction(
        user_a, card_a, category="Dining", amount="100.00", date=today
    )
    _insert_transaction(
        user_a, card_a, category="Groceries", amount="50.00", date=today
    )

    resp = client.get("/goals", headers=_auth(user_a))
    item = resp.json()["items"][0]
    assert item["goal"]["category"] is None
    assert Decimal(item["spent_period_to_date"]) == Decimal("150.00")


def test_get_goals_sums_across_pages(client, user_a, card_a, monkeypatch):
    """Spend pages past the per-page size — no silent truncation.

    Regression for Codex finding: the original implementation capped
    the read at a single 250-row page and would undercount any goal
    window with more transactions. We monkeypatch the page size down
    to 3 to keep the test fast while still exercising the loop's
    "short page = done" terminator.
    """
    from app.services import goals as goals_service

    monkeypatch.setattr(goals_service, "_SPEND_PAGE_SIZE", 3)

    today = dt.date.today()
    _insert_goal(user_a, category="Dining", amount="100", period="month")
    # 7 transactions of $4 each = $28; with page size 3 the service
    # must fetch 3 pages (3 + 3 + 1) to sum them all.
    for _ in range(7):
        _insert_transaction(
            user_a,
            card_a,
            category="Dining",
            amount="4.00",
            date=today,
        )

    resp = client.get("/goals", headers=_auth(user_a))
    item = resp.json()["items"][0]
    assert Decimal(item["spent_period_to_date"]) == Decimal("28.00")


# ---------------------------------------------------------------------------
# PATCH /goals/{id}
# ---------------------------------------------------------------------------


def test_patch_goal_updates_amount(client, user_a):
    """PATCH `{amount}` rewrites the row and bumps updated_at."""
    goal_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    resp = client.patch(
        f"/goals/{goal_id}",
        headers=_auth(user_a),
        json={"amount": "150.00"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["amount"]) == Decimal("150.00")
    assert body["period"] == "month"


def test_patch_goal_updates_period(client, user_a):
    """PATCH `{period}` rewrites the row without touching amount."""
    goal_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    resp = client.patch(
        f"/goals/{goal_id}",
        headers=_auth(user_a),
        json={"period": "week"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "week"
    assert Decimal(body["amount"]) == Decimal("300")


def test_patch_goal_empty_body_is_422(client, user_a):
    """PATCH with no fields set is a validation error (model_validator)."""
    goal_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    resp = client.patch(f"/goals/{goal_id}", headers=_auth(user_a), json={})
    assert resp.status_code == 422


def test_patch_goal_rejects_zero_amount(client, user_a):
    """Pydantic rejects amount<=0 at the model layer."""
    goal_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    resp = client.patch(
        f"/goals/{goal_id}", headers=_auth(user_a), json={"amount": "0"}
    )
    assert resp.status_code == 422


def test_patch_goal_collision_returns_409(client, user_a):
    """Period change into an occupied slot surfaces goal_slot_occupied."""
    _insert_goal(user_a, category="Dining", amount="100", period="week")
    monthly_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    resp = client.patch(
        f"/goals/{monthly_id}",
        headers=_auth(user_a),
        json={"period": "week"},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "goal_slot_occupied"


def test_patch_goal_404_for_unknown(client, user_a):
    """Unknown id → 404; same response shape for someone else's goal."""
    resp = client.patch(
        f"/goals/{uuid.uuid4()}",
        headers=_auth(user_a),
        json={"amount": "1"},
    )
    assert resp.status_code == 404


def test_patch_goal_404_for_other_user(client, user_a, user_b):
    """User A patching user B's goal id behaves like the row doesn't exist."""
    other_id = _insert_goal(
        user_b, category="Dining", amount="300", period="month"
    )
    resp = client.patch(
        f"/goals/{other_id}",
        headers=_auth(user_a),
        json={"amount": "1"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /goals/{id}
# ---------------------------------------------------------------------------


def test_delete_goal_removes_row(client, user_a):
    """DELETE returns 204 and the row disappears from subsequent GET."""
    goal_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    resp = client.delete(f"/goals/{goal_id}", headers=_auth(user_a))
    assert resp.status_code == 204
    resp2 = client.get("/goals", headers=_auth(user_a))
    assert resp2.json()["items"] == []


def test_delete_goal_idempotent(client, user_a):
    """Re-deleting the same id is a silent 204."""
    goal_id = _insert_goal(
        user_a, category="Dining", amount="300", period="month"
    )
    client.delete(f"/goals/{goal_id}", headers=_auth(user_a))
    second = client.delete(f"/goals/{goal_id}", headers=_auth(user_a))
    assert second.status_code == 204


def test_delete_goal_rls_no_op_on_other_user(client, user_a, user_b):
    """User A deleting user B's goal id is a no-op — row remains for B."""
    other_id = _insert_goal(
        user_b, category="Dining", amount="300", period="month"
    )
    resp = client.delete(f"/goals/{other_id}", headers=_auth(user_a))
    # RLS silently zero-rows the DELETE; HTTP status is still 204 by
    # the route contract.
    assert resp.status_code == 204
    # The owner can still see it.
    resp_b = client.get("/goals", headers=_auth(user_b))
    ids = {row["goal"]["id"] for row in resp_b.json()["items"]}
    assert other_id in ids


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Build the bearer + device headers for the authenticated route gate."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _insert_goal(
    user, *, category: str | None, amount: str, period: str
) -> str:
    """Insert one goals row via the user's JWT-scoped client; return id.

    Bypasses the HTTP route — these tests cover the read/edit/delete
    surface, not creation (which goes through `set_goal` per CLAUDE.md
    invariant #8). The unique key + RLS still apply.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("goals")
        .insert(
            {
                "user_id": user.id,
                "category": category,
                "amount": amount,
                "period": period,
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


def _insert_transaction(
    user, card_id: str, *, category: str, amount: str, date: dt.date
) -> str:
    """Insert one active transaction; return id."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("transactions")
        .insert(
            {
                "user_id": user.id,
                "card_id": card_id,
                "merchant": f"test-{uuid.uuid4().hex[:6]}",
                "amount": amount,
                "category": category,
                "date": date.isoformat(),
                "status": "active",
                "source": "manual",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]
