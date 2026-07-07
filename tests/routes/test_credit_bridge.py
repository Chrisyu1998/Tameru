"""Phase 2 — ledger-bridge contract suite (DESIGN.md §6.7).

The bridge: a `POST /transactions/confirm` whose merchant + card match an active
statement credit returns a `credit_suggestion` (a SEPARATE field from
`insight`); a tap on `POST /card-credits/{id}/apply` counts it via the atomic
`card_credit_apply_usage` RPC. Covers the TODO.md Phase-2 done-when list:

- match → suggestion present; no match → null; idempotent re-confirm → null.
- apply increments used_amount; over-cap clamps at the allowance; a refund
  floors at 0; a spend dated before the current period is a no-op (period guard).

Credits are seeded through the real `POST /card-credits/confirm` path with a
per-test-tagged `merchant_hint` so the substring match only hits this test's
credit on the session-scoped `card_a`.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient reusing the running stack from conftest."""
    return TestClient(app)


def test_matching_transaction_returns_credit_suggestion(client, user_a, card_a):
    """A charge whose merchant contains a credit's hint yields a suggestion."""
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"lululemon{tag}", amount="75")
    resp = _confirm_txn(
        client, user_a, merchant=f"Lululemon{tag} ATL", card_id=card_a, amount="30"
    )
    cs = resp["credit_suggestion"]
    assert cs is not None, resp
    assert cs["credit_id"] == credit["id"]
    assert cs["credit_name"] == credit["name"]
    assert cs["transaction_id"] == resp["transaction"]["id"]
    # suggested = min(txn 30, remaining 75) = 30; remaining = 75 - 0.
    assert _dec(cs["suggested_amount"]) == 30
    assert _dec(cs["remaining"]) == 75


def test_non_matching_transaction_returns_null(client, user_a, card_a):
    """A charge that matches no credit hint carries `credit_suggestion=null`."""
    tag = _tag()
    _seed_credit(client, user_a, card_a, hint=f"lululemon{tag}", amount="75")
    resp = _confirm_txn(
        client, user_a, merchant=f"Starbucks{tag}", card_id=card_a, amount="6"
    )
    assert resp["credit_suggestion"] is None


def test_idempotent_reconfirm_returns_null(client, user_a, card_a):
    """A replayed confirm (same crid) suppresses the suggestion, like insight."""
    tag = _tag()
    _seed_credit(client, user_a, card_a, hint=f"resy{tag}", amount="100")
    crid = str(uuid.uuid4())
    first = _confirm_txn(
        client, user_a, merchant=f"Resy{tag} Dinner", card_id=card_a,
        amount="40", crid=crid,
    )
    assert first["credit_suggestion"] is not None
    replay = _confirm_txn(
        client, user_a, merchant=f"Resy{tag} Dinner", card_id=card_a,
        amount="40", crid=crid,
    )
    assert replay["credit_suggestion"] is None
    assert replay["insight"] is None


def test_apply_increments_used_amount(client, user_a, card_a):
    """Tapping the suggestion counts the transaction toward the credit."""
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"saks{tag}", amount="50")
    resp = _confirm_txn(
        client, user_a, merchant=f"Saks{tag}", card_id=card_a, amount="20"
    )
    cs = resp["credit_suggestion"]
    applied = client.post(
        f"/card-credits/{cs['credit_id']}/apply",
        headers=_auth(user_a),
        json={"transaction_id": cs["transaction_id"]},
    )
    assert applied.status_code == 200, applied.text
    assert _dec(applied.json()["used_amount"]) == 20


def test_apply_is_idempotent_per_transaction(client, user_a, card_a):
    """Re-applying the SAME transaction is a no-op (Codex 2026-07-05).

    A lost-response retry / double-tap / offline replay must not count the same
    purchase twice: the (credit, transaction) ledger dedupes it, and each call
    returns 200 with the (unchanged) credit so the retry resolves cleanly.
    """
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"dup{tag}", amount="75")
    resp = _confirm_txn(
        client, user_a, merchant=f"Dup{tag} Store", card_id=card_a, amount="30"
    )
    cs = resp["credit_suggestion"]
    body = {"transaction_id": cs["transaction_id"]}
    first = client.post(
        f"/card-credits/{cs['credit_id']}/apply", headers=_auth(user_a), json=body
    )
    second = client.post(
        f"/card-credits/{cs['credit_id']}/apply", headers=_auth(user_a), json=body
    )
    assert first.status_code == 200 and second.status_code == 200, second.text
    # Counted once, not twice — both responses show used_amount = 30.
    assert _dec(first.json()["used_amount"]) == 30
    assert _dec(second.json()["used_amount"]) == 30


def test_apply_rejects_out_of_period_transaction(client, user_a, card_a):
    """A transaction dated before the credit's current period is never counted.

    The period guard lives in BOTH the idempotency claim and the increment; the
    increment's guard is re-checked under the row lock (EvalPlanQual) so a
    concurrent reset that advances the period between an apply's snapshot and its
    write can't slip an old-period spend into the fresh period (Codex
    2026-07-05). Here we exercise the deterministic guard directly: a spend
    dated before `current_period_start` → 409, used_amount untouched.
    """
    tag = _tag()
    # Credit whose current period starts in the FUTURE relative to the charge.
    credit_id = _seed_credit_direct(
        user_a,
        card_a,
        name=f"OOP-{tag}",
        amount="75",
        current_period_start=(_dt.date.today() + _dt.timedelta(days=10)).isoformat(),
        next_reset=(_dt.date.today() + _dt.timedelta(days=100)).isoformat(),
    )
    # A charge dated today — before current_period_start → out of period. (No
    # suggestion fires either, by the same guard; we apply directly to prove the
    # endpoint rejects it.)
    txn = _confirm_txn(
        client, user_a, merchant=f"OOP{tag}", card_id=card_a, amount="30"
    )
    denied = client.post(
        f"/card-credits/{credit_id}/apply",
        headers=_auth(user_a),
        json={"transaction_id": txn["transaction"]["id"]},
    )
    assert denied.status_code == 409, denied.text
    row = (
        supabase_for_user(user_a.jwt)
        .table("card_credits")
        .select("used_amount")
        .eq("id", credit_id)
        .execute()
    )
    assert _dec(row.data[0]["used_amount"]) == 0


def test_apply_over_cap_clamps_at_allowance(client, user_a, card_a):
    """Applying a transaction larger than the remaining allowance clamps."""
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"uber{tag}", amount="15")
    resp = _confirm_txn(
        client, user_a, merchant=f"Uber{tag} Eats", card_id=card_a, amount="80"
    )
    cs = resp["credit_suggestion"]
    # Display is already clamped: min(80, remaining 15) = 15.
    assert _dec(cs["suggested_amount"]) == 15
    applied = client.post(
        f"/card-credits/{cs['credit_id']}/apply",
        headers=_auth(user_a),
        json={"transaction_id": cs["transaction_id"]},
    )
    assert applied.status_code == 200, applied.text
    assert _dec(applied.json()["used_amount"]) == 15  # clamped, not 80


def test_apply_refund_floors_at_zero(client, user_a, card_a):
    """A refund (negative amount) applied to a credit floors used_amount at 0."""
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"airline{tag}", amount="200")
    # Charge $120 toward it first.
    charge = _confirm_txn(
        client, user_a, merchant=f"Airline{tag}", card_id=card_a, amount="120"
    )
    client.post(
        f"/card-credits/{credit['id']}/apply",
        headers=_auth(user_a),
        json={"transaction_id": charge["credit_suggestion"]["transaction_id"]},
    )
    # Now a $200 refund on the same card, applied directly (refunds don't
    # produce a suggestion, but the apply RPC must floor the result at 0).
    refund_id = _insert_refund(user_a, card_a, amount="-200")
    applied = client.post(
        f"/card-credits/{credit['id']}/apply",
        headers=_auth(user_a),
        json={"transaction_id": refund_id},
    )
    assert applied.status_code == 200, applied.text
    assert _dec(applied.json()["used_amount"]) == 0  # 120 - 200, floored at 0


def test_spend_before_current_period_is_no_suggestion(client, user_a, card_a):
    """A charge dated before the credit's current period doesn't get offered."""
    tag = _tag()
    _seed_credit(
        client, user_a, card_a, hint=f"monthly{tag}", amount="25", cadence="monthly"
    )
    # A monthly credit's current period starts on the 1st; date 40 days ago is
    # in a prior period → the lower-bound guard skips it.
    old = _dt.date.today() - _dt.timedelta(days=40)
    resp = _confirm_txn(
        client, user_a, merchant=f"Monthly{tag} Shop", card_id=card_a,
        amount="10", txn_date=old,
    )
    assert resp["credit_suggestion"] is None


def test_fully_used_credit_is_not_offered(client, user_a, card_a):
    """A credit already at its allowance yields no suggestion."""
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"maxed{tag}", amount="30")
    # Max it out via the set-used-amount PATCH.
    client.patch(
        f"/card-credits/{credit['id']}",
        headers=_auth(user_a),
        json={"used_amount": "30"},
    )
    resp = _confirm_txn(
        client, user_a, merchant=f"Maxed{tag} Store", card_id=card_a, amount="10"
    )
    assert resp["credit_suggestion"] is None


def test_apply_foreign_credit_is_409(client, user_a, user_b, card_a, card_b):
    """User B cannot count a transaction toward user A's credit (RLS)."""
    tag = _tag()
    credit = _seed_credit(client, user_a, card_a, hint=f"rls{tag}", amount="50")
    resp = _confirm_txn(
        client, user_a, merchant=f"Rls{tag}", card_id=card_a, amount="20"
    )
    txn_id = resp["credit_suggestion"]["transaction_id"]
    # User B aims A's transaction at A's credit — both hidden by RLS → empty → 409.
    denied = client.post(
        f"/card-credits/{credit['id']}/apply",
        headers=_auth(user_b),
        json={"transaction_id": txn_id},
    )
    assert denied.status_code == 409, denied.text
    # And A's credit is untouched.
    still = supabase_for_user(user_a.jwt).table("card_credits").select(
        "used_amount"
    ).eq("id", credit["id"]).execute()
    assert _dec(still.data[0]["used_amount"]) == 0


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _seed_credit(
    client, user, card_id, *, hint, amount, name=None, cadence="quarterly"
) -> dict:
    """Create one active credit on `card_id` via the real confirm path.

    Returns the created row. `hint` is the merchant token the bridge matches
    on; `name` defaults to a hint-derived label so the (card_id, lower(name))
    partial index doesn't collide across tests on the shared card.
    """
    body = {
        "credits": [
            {
                "card_id": card_id,
                "name": name or f"{hint} credit",
                "amount": amount,
                "cadence": cadence,
                "merchant_hint": hint,
                "source_urls": [],
                "verified_at": None,
                "client_request_id": str(uuid.uuid4()),
            }
        ]
    }
    resp = client.post("/card-credits/confirm", headers=_auth(user), json=body)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "credit seed landed no row"
    return items[0]


def _seed_credit_direct(
    user, card_id, *, name, amount, current_period_start, next_reset, cadence="quarterly"
) -> str:
    """Insert a credit directly under RLS with explicit period dates.

    The confirm route seeds `current_period_start` from `credit_period_bounds`
    on today, so a direct insert is the only way to construct a credit whose
    current period doesn't contain a given transaction date (the out-of-period /
    reset-race scenario). Returns the new credit id.
    """
    row = (
        supabase_for_user(user.jwt)
        .table("card_credits")
        .insert(
            {
                "user_id": user.id,
                "card_id": card_id,
                "name": name,
                "amount": amount,
                "cadence": cadence,
                "used_amount": "0",
                "current_period_start": current_period_start,
                "next_reset_date": next_reset,
                "status": "active",
            }
        )
        .execute()
    )
    return row.data[0]["id"]


def _confirm_txn(
    client, user, *, merchant, card_id, amount="30", crid=None, txn_date=None
) -> dict:
    """Confirm a transaction and return the response JSON."""
    body = {
        "merchant": merchant,
        "amount": amount,
        "date": (txn_date or _dt.date.today()).isoformat(),
        "category": "Shopping",
        "card_id": card_id,
        "client_request_id": crid or str(uuid.uuid4()),
    }
    resp = client.post("/transactions/confirm", headers=_auth(user), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _insert_refund(user, card_id, *, amount) -> str:
    """Insert a refund transaction (negative amount) directly under RLS.

    Refunds don't produce a bridge suggestion, so this seeds one so the apply
    RPC's GREATEST(0, …) floor can be exercised.
    """
    client = supabase_for_user(user.jwt)
    row = (
        client.table("transactions")
        .insert(
            {
                "user_id": user.id,
                "card_id": card_id,
                "merchant": f"Refund-{_tag()}",
                "amount": amount,
                "date": _dt.date.today().isoformat(),
                "category": "Shopping",
                "source": "nlp",
                "status": "active",
                "client_request_id": str(uuid.uuid4()),
            }
        )
        .execute()
    )
    return row.data[0]["id"]


def _auth(user) -> dict[str, str]:
    """Bearer + device-id headers the device-gated routes require."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _tag() -> str:
    """Short unique suffix to keep credit names / hints per-test."""
    return uuid.uuid4().hex[:8]


def _dec(value) -> float:
    """Parse a Decimal-string wire value for numeric comparison."""
    return float(value)
