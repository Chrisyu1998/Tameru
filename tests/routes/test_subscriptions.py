"""Day 19 — subscriptions API contract suite.

Covers the deliverables from prompt/week-3-polish-and-extras/day-19-subscriptions-pgcron.md:

- `POST /subscriptions/confirm`: happy path for cardful and cardless
  proposals; idempotency keyed on `client_request_id`; ownership check
  for `card_id`; validation errors (amount, frequency, category).
- `GET /subscriptions?status=`: filter to active / paused / cancelled /
  all; default `status=active`.
- `PATCH /subscriptions/{id}`: edits to amount/category/name/card_id/
  status; rejects `frequency` and `start_date` (immutability rule —
  DESIGN.md §8.3); card-reassignment ownership re-check.
- `DELETE /subscriptions/{id}`: soft cancel.
- RLS: user A cannot GET / PATCH / DELETE user B's subscriptions.

Each test generates a unique subscription name so session-scoped
fixtures (user_a, card_a) aren't contaminated across tests.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client that re-uses the running stack from conftest."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /subscriptions/confirm
# ---------------------------------------------------------------------------


def test_confirm_cardful_proposal_creates_row(client, user_a, card_a):
    """Verify a cardful proposal commits with all fields intact."""
    crid = str(uuid.uuid4())
    name = f"Netflix-{_tag()}"
    today = date.today()
    body = _proposal(
        name=name,
        card_id=card_a,
        client_request_id=crid,
        next_billing_date=today + timedelta(days=30),
    )
    resp = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200, resp.text
    sub = resp.json()
    assert sub["name"] == name
    assert sub["card_id"] == card_a
    assert sub["client_request_id"] == crid
    assert sub["status"] == "active"
    assert sub["frequency"] == "monthly"


def test_confirm_cardless_proposal_creates_row(client, user_a):
    """Cardless ACH subscription commits with `card_id=null`."""
    crid = str(uuid.uuid4())
    body = _proposal(
        name=f"Rent-{_tag()}",
        card_id=None,
        client_request_id=crid,
        amount="2400",
        category="Home",
    )
    resp = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200, resp.text
    sub = resp.json()
    assert sub["card_id"] is None
    assert sub["client_request_id"] == crid


def test_confirm_idempotent_on_same_crid(client, user_a, card_a):
    """Two POSTs with the same `client_request_id` return the same row.

    Without this, a Day 15 offline-queue drain retry would create a
    duplicate subscription and pg_cron would auto-log a duplicate
    transaction every billing cycle.
    """
    crid = str(uuid.uuid4())
    body = _proposal(name=f"Idem-{_tag()}", card_id=card_a, client_request_id=crid)
    first = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert first.status_code == 200
    second = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_confirm_rejects_other_users_card_id(client, user_a, card_b):
    """A tampered client posting another user's card_id is rejected.

    RLS scopes `subscriptions` writes by `user_id`, but the
    `card_id` FK is not user-bound at the DB layer. The route-level
    `_assert_card_owned` check is the defense.
    """
    body = _proposal(
        name=f"Cross-{_tag()}",
        card_id=card_b,  # belongs to user_b
        client_request_id=str(uuid.uuid4()),
    )
    resp = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_card"


def test_confirm_rejects_non_positive_amount(client, user_a, card_a):
    """`amount <= 0` is rejected at the model layer."""
    body = _proposal(
        name=f"Zero-{_tag()}",
        card_id=card_a,
        amount="0",
        client_request_id=str(uuid.uuid4()),
    )
    resp = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 422


def test_confirm_rejects_invalid_frequency(client, user_a, card_a):
    """Frequency outside the closed enum is rejected."""
    body = _proposal(
        name=f"BadFreq-{_tag()}",
        card_id=card_a,
        client_request_id=str(uuid.uuid4()),
    )
    body["frequency"] = "daily"  # not in the enum
    resp = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 422


def test_confirm_rejects_invalid_category(client, user_a, card_a):
    """Category outside `ALLOWED_CATEGORIES` is rejected."""
    body = _proposal(
        name=f"BadCat-{_tag()}",
        card_id=card_a,
        client_request_id=str(uuid.uuid4()),
    )
    body["category"] = "Fees"  # not in ALLOWED_CATEGORIES — Day 19b lesson learned
    resp = client.post("/subscriptions/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /subscriptions
# ---------------------------------------------------------------------------


def test_get_subscriptions_default_returns_only_active(client, user_a, card_a):
    """Default `?status=active` filter excludes paused / cancelled rows."""
    active_id = _create_subscription(
        client, user_a, card_a, name=f"Active-{_tag()}"
    )
    paused_id = _create_subscription(
        client, user_a, card_a, name=f"Paused-{_tag()}"
    )
    client.patch(
        f"/subscriptions/{paused_id}",
        headers=_auth(user_a),
        json={"status": "paused"},
    )

    resp = client.get("/subscriptions", headers=_auth(user_a))
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["items"]}
    assert active_id in ids
    assert paused_id not in ids


def test_get_subscriptions_all_status_returns_every_row(client, user_a, card_a):
    """`?status=all` returns active + paused + cancelled."""
    paused_id = _create_subscription(
        client, user_a, card_a, name=f"AllPaused-{_tag()}"
    )
    client.patch(
        f"/subscriptions/{paused_id}",
        headers=_auth(user_a),
        json={"status": "paused"},
    )
    resp = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all"},
    )
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["items"]}
    assert paused_id in ids


def test_get_subscriptions_hides_card_af_by_default(client, user_a, card_a):
    """The default response excludes card annual-fee companion rows.

    DESIGN.md §6.5 — AF rows are conceptually a card consequence, not
    a user-tracked subscription. Surfacing them next to Netflix on
    `/subscriptions` would conflate two different concepts and offer a
    misleading "pause" affordance on a charge the user can't actually
    pause without cancelling the card.

    Recognition uses the same triple as the soft-delete cascade:
    `name LIKE '% annual fee'` + `category='Memberships'` +
    `frequency='annual'`.
    """
    netflix_id = _create_subscription(
        client, user_a, card_a, name=f"Netflix-{_tag()}"
    )
    af_id = _create_subscription(
        client,
        user_a,
        card_a,
        name=f"AF-{_tag()} annual fee",
        frequency="annual",
        category="Memberships",
    )

    # Default response: no AF rows.
    default = client.get("/subscriptions", headers=_auth(user_a)).json()
    ids = {row["id"] for row in default["items"]}
    assert netflix_id in ids
    assert af_id not in ids


def test_get_subscriptions_include_card_af_returns_them(client, user_a, card_a):
    """`?include_card_af=true` surfaces AF rows for the cards-list chip."""
    af_id = _create_subscription(
        client,
        user_a,
        card_a,
        name=f"OptIn-{_tag()} annual fee",
        frequency="annual",
        category="Memberships",
    )
    resp = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"include_card_af": "true"},
    )
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["items"]}
    assert af_id in ids


# ---------------------------------------------------------------------------
# PATCH /subscriptions/{id} — immutability + edits
# ---------------------------------------------------------------------------


def test_patch_rejects_frequency_change(client, user_a, card_a):
    """`frequency` is immutable post-create — PATCH rejects it.

    DESIGN.md §8.3 — to change cadence, cancel and re-add. The model
    layer's `extra='forbid'` produces a 422 with a Pydantic-shaped
    validation error.
    """
    sub_id = _create_subscription(client, user_a, card_a, name=f"NoFreq-{_tag()}")
    resp = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"frequency": "annual"},
    )
    assert resp.status_code == 422


def test_patch_rejects_start_date_change(client, user_a, card_a):
    """`start_date` is immutable post-create — PATCH rejects it."""
    sub_id = _create_subscription(client, user_a, card_a, name=f"NoStart-{_tag()}")
    resp = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"start_date": "2026-01-01"},
    )
    assert resp.status_code == 422


def test_patch_accepts_amount_change(client, user_a, card_a):
    """Amount edits succeed (CSR $550 → $795 pattern from Day 19b)."""
    sub_id = _create_subscription(client, user_a, card_a, name=f"Amt-{_tag()}")
    resp = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"amount": "19.99"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["amount"] == "19.99"


def test_patch_card_id_reassignment_rechecks_ownership(client, user_a, card_a, card_b):
    """PATCHing `card_id` to another user's card returns 422."""
    sub_id = _create_subscription(client, user_a, card_a, name=f"Rea-{_tag()}")
    resp = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"card_id": card_b},  # user_b's card
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_card"


def test_patch_card_id_to_null_allowed(client, user_a, card_a):
    """Setting `card_id=null` is allowed (re-pointing to bank ACH)."""
    sub_id = _create_subscription(client, user_a, card_a, name=f"ToAch-{_tag()}")
    resp = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"card_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["card_id"] is None


def test_patch_rejects_revival_of_cancelled_subscription(client, user_a, card_a):
    """Cancelled is terminal — PATCH cannot reactivate a cancelled row.

    DESIGN.md §8.3 cancel/re-add doctrine. Reviving a cancelled
    subscription would make pg_cron start auto-logging it again,
    contradicting the user's intent and the supported "cancel-then-
    re-add as a new row" flow.
    """
    sub_id = _create_subscription(client, user_a, card_a, name=f"Term-{_tag()}")
    cancel = client.delete(f"/subscriptions/{sub_id}", headers=_auth(user_a))
    assert cancel.status_code == 204

    # Any PATCH on a cancelled row 422s, including status='active'.
    revive = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"status": "active"},
    )
    assert revive.status_code == 422
    assert revive.json()["detail"]["code"] == "terminal_status"

    # Confirm the row stays cancelled.
    listed = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "cancelled"},
    )
    target = next(row for row in listed.json()["items"] if row["id"] == sub_id)
    assert target["status"] == "cancelled"


def test_patch_rejects_resume_with_deleted_backing_card(client, user_a):
    """Resuming a paused-by-card-deletion sub without reassigning 422s.

    DESIGN.md §8.3 — the split-cascade leaves `card_id` pointing at the
    closed card. Without the resume guard, a bare `{"status":"active"}`
    PATCH would let pg_cron auto-log charges onto a soft-deleted card.
    The user is forced to reassign (PATCH `card_id`) or clear to ACH
    (`card_id: null`) before resuming.
    """
    from uuid import uuid4

    # Add a dedicated card for this test so we can soft-delete it
    # without disturbing the session-scoped card_a fixture.
    tag = _tag()
    card_body = {
        "network": "visa",
        "last_four": "9" + (tag[:3] if tag[:3].isdigit() else "100"),
        "name": f"ResumeGuard-{tag}",
        "issuer": "capital_one",
        "program": "Other",
        "multipliers": {},
        "source_urls": [],
        "needs_manual": False,
        "client_request_id": str(uuid4()),
    }
    card_resp = client.post("/cards/confirm", headers=_auth(user_a), json=card_body)
    assert card_resp.status_code == 200, card_resp.text
    card_id = card_resp.json()["id"]

    sub_id = _create_subscription(
        client,
        user_a,
        card_id,
        name=f"NeedsCard-{_tag()}",
    )

    # Soft-delete the card — cascade flips the sub to paused.
    delete = client.delete(f"/cards/{card_id}", headers=_auth(user_a))
    assert delete.status_code == 204

    # Bare resume → 422 with card_deleted.
    bare = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"status": "active"},
    )
    assert bare.status_code == 422
    assert bare.json()["detail"]["code"] == "card_deleted"

    # Recovery path 1: clear to bank ACH + resume in one PATCH succeeds.
    ach = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_a),
        json={"card_id": None, "status": "active"},
    )
    assert ach.status_code == 200, ach.text
    assert ach.json()["status"] == "active"
    assert ach.json()["card_id"] is None


def test_confirm_replay_after_cancel_creates_fresh_active_row(
    client, user_a, card_a
):
    """Same-crid replay AFTER cancellation creates a fresh active row.

    DESIGN.md §8.3 cancel/re-add doctrine: cancelled is terminal, but
    a same-payload replay of `/subscriptions/confirm` (e.g. an offline
    queue retry whose original commit succeeded but whose ACK was lost
    and where the user then cancelled in another tab) should still
    produce an active subscription rather than 23505 on the unique
    index. The crid partial unique index is scoped to `status <>
    'cancelled'` for exactly this reason.
    """
    crid = str(uuid.uuid4())
    proposal = _proposal(
        name=f"Replay-{_tag()}",
        card_id=card_a,
        client_request_id=crid,
    )
    first = client.post(
        "/subscriptions/confirm", headers=_auth(user_a), json=proposal
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    # Cancel the first row.
    cancel = client.delete(
        f"/subscriptions/{first_id}", headers=_auth(user_a)
    )
    assert cancel.status_code == 204

    # Replay the same crid. The route's idempotency lookup excludes
    # cancelled rows, so it inserts a fresh active row. The partial
    # unique index (now scoped to non-cancelled) lets it succeed.
    replay = client.post(
        "/subscriptions/confirm", headers=_auth(user_a), json=proposal
    )
    assert replay.status_code == 200, replay.text
    replay_id = replay.json()["id"]
    assert replay_id != first_id
    assert replay.json()["status"] == "active"


# ---------------------------------------------------------------------------
# DELETE /subscriptions/{id}
# ---------------------------------------------------------------------------


def test_delete_soft_cancels(client, user_a, card_a):
    """DELETE flips `status='cancelled'`; the row stays in the table."""
    sub_id = _create_subscription(client, user_a, card_a, name=f"Del-{_tag()}")
    resp = client.delete(f"/subscriptions/{sub_id}", headers=_auth(user_a))
    assert resp.status_code == 204

    # Re-read via GET with `?status=cancelled` to confirm the row stayed.
    listed = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "cancelled"},
    )
    ids = {row["id"] for row in listed.json()["items"]}
    assert sub_id in ids


# ---------------------------------------------------------------------------
# RLS — cross-user isolation
# ---------------------------------------------------------------------------


def test_rls_user_b_cannot_see_user_a_subscription(client, user_a, card_a, user_b):
    """User B's GET / PATCH / DELETE on user A's subscription returns empty.

    RLS scopes the SELECT to `auth.uid()` so user B sees zero rows and
    the per-id GET 404s.
    """
    sub_id = _create_subscription(client, user_a, card_a, name=f"Rls-{_tag()}")
    # User B's list does not contain it.
    resp = client.get(
        "/subscriptions",
        headers=_auth(user_b),
        params={"status": "all"},
    )
    ids = {row["id"] for row in resp.json()["items"]}
    assert sub_id not in ids

    # User B's PATCH on user A's id returns 404 (RLS-empty row vanishes
    # before the route's existence check).
    resp = client.patch(
        f"/subscriptions/{sub_id}",
        headers=_auth(user_b),
        json={"amount": "0.01"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Same shape as tests/routes/test_transactions.py::_auth."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _tag() -> str:
    """Per-test merchant tag so session fixtures don't collide."""
    return uuid.uuid4().hex[:8]


def _proposal(
    *,
    name: str,
    card_id: str | None,
    client_request_id: str,
    amount: str = "15.99",
    frequency: str = "monthly",
    start_date: date | None = None,
    next_billing_date: date | None = None,
    category: str = "Streaming",
) -> dict:
    """Build a SubscriptionConfirmRequest-shaped body."""
    sd = start_date or date.today()
    nbd = next_billing_date or (date.today() + timedelta(days=30))
    body: dict = {
        "name": name,
        "amount": amount,
        "frequency": frequency,
        "start_date": sd.isoformat(),
        "next_billing_date": nbd.isoformat(),
        "category": category,
        "card_id": card_id,
        "client_request_id": client_request_id,
    }
    return body


def _create_subscription(
    client: TestClient,
    user,
    card_id: str,
    *,
    name: str,
    frequency: str = "monthly",
    category: str = "Streaming",
) -> str:
    """Convenience: POST a fresh subscription and return its id.

    `frequency` and `category` are optional — tests that need to seed
    an AF-shaped row (annual + Memberships category + 'X annual fee'
    name) override them to exercise the hide-AF filter.
    """
    resp = client.post(
        "/subscriptions/confirm",
        headers=_auth(user),
        json=_proposal(
            name=name,
            card_id=card_id,
            client_request_id=str(uuid.uuid4()),
            frequency=frequency,
            category=category,
        ),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]
