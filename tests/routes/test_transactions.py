"""Day 5 — transactions API contract suite.

Covers the deliverables from prompt/week-1-foundation/day-05-transactions-api.md:

- `POST /transactions/confirm`: happy path, validation errors (enum, amount,
  date, card ownership), `client_request_id` idempotency, `merchant_category`
  upsert on override, no upsert on confirmation.
- `PATCH /transactions/{id}`: category upsert rule (fires on category change
  only; keyed on new merchant if both change; merchant-only PATCH does not
  touch `merchant_category`).
- `GET /transactions`: filter combinations, ordering, pagination boundaries,
  silent `limit` clamp.
- `DELETE /transactions/{id}`: soft-delete (status='deleted'), RLS-scoped.
- Service-layer parity: `list_transactions()` matches the HTTP route for the
  same filters.
- RLS: user A cannot GET / PATCH / DELETE user B's transactions.

Each test generates a unique merchant tag so session-scoped fixtures
(user_a, card_a) aren't contaminated by artifacts from earlier tests.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.main import app
from app.models.transactions import TransactionFilters
from app.services.transactions import list_transactions
from app.util.merchant import normalize_merchant


@pytest.fixture
def client() -> TestClient:
    """Provide client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /transactions/confirm — happy path + server-hardcoded source
# ---------------------------------------------------------------------------


def test_confirm_creates_row_with_server_hardcoded_source(client, user_a, card_a):
    """Verify that confirm creates row with server hardcoded source."""
    merchant = f"Shop-{_tag()}"
    crid = str(uuid.uuid4())
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=merchant,
            card_id=card_a,
            gemini_suggestion="Groceries",
            category="Groceries",
            client_request_id=crid,
        ),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # `insight` is governed by Day 13's rule engine — this test only
    # cares that the row commits with the server-hardcoded source. The
    # field shape (str | null) is verified separately in the Day 13
    # insight tests below.
    assert "insight" in body

    tx = body["transaction"]
    assert tx["merchant"] == merchant
    assert tx["source"] == "nlp"
    assert tx["gemini_suggestion"] == "Groceries"
    assert tx["category"] == "Groceries"
    assert tx["client_request_id"] == crid
    assert tx["card_id"] == card_a


# ---------------------------------------------------------------------------
# POST /transactions/confirm — validation errors
# ---------------------------------------------------------------------------


def test_confirm_rejects_category_outside_closed_enum(client, user_a, card_a):
    """Verify that confirm rejects category outside closed enum."""
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=f"Shop-{_tag()}",
            card_id=card_a,
            category="NotARealCategory",
        ),
    )
    assert resp.status_code == 422
    body = resp.text.lower()
    assert "category" in body


def test_confirm_rejects_non_positive_amount(client, user_a, card_a):
    """Verify that confirm rejects non positive amount."""
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=f"Shop-{_tag()}",
            card_id=card_a,
            amount="0",
        ),
    )
    assert resp.status_code == 422
    assert "amount" in resp.text.lower()


def test_confirm_rejects_far_future_date(client, user_a, card_a):
    """Verify that confirm rejects far future date."""
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=f"Shop-{_tag()}",
            card_id=card_a,
            txn_date=date.today() + timedelta(days=30),
        ),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["code"] == "invalid_date"


def test_confirm_rejects_whitespace_only_merchant(client, user_a, card_a):
    """`Field(min_length=1)` alone would let '   ' through — explicit
    validator catches it. (P2 fix.)"""
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(merchant="   ", card_id=card_a),
    )
    assert resp.status_code == 422


def test_confirm_trims_leading_trailing_whitespace_on_merchant(
    client, user_a, card_a
):
    """Verify that confirm trims leading trailing whitespace on merchant."""
    merchant_raw = f"   TrimShop-{_tag()}   "
    merchant_expected = merchant_raw.strip()
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(merchant=merchant_raw, card_id=card_a),
    )
    assert resp.status_code == 200
    assert resp.json()["transaction"]["merchant"] == merchant_expected


def test_patch_rejects_whitespace_only_merchant(client, user_a, card_a):
    """Verify that patch rejects whitespace only merchant."""
    merchant = f"WsPatch-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"merchant": "   "},
    )
    assert resp.status_code == 422


def test_confirm_rejects_foreign_card_id(client, user_a, card_b):
    """Verify that confirm rejects foreign card id."""
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=f"Shop-{_tag()}",
            card_id=card_b,  # user B's card!
        ),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["code"] == "invalid_card"


# ---------------------------------------------------------------------------
# POST /transactions/confirm — merchant_category upsert rules
# ---------------------------------------------------------------------------


def test_confirm_on_override_upserts_merchant_category(client, user_a, card_a):
    """Verify that confirm on override upserts merchant category."""
    merchant = f"OverrideShop-{_tag()}"
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=merchant,
            card_id=card_a,
            gemini_suggestion="Dining",
            category="Coffee Shops",  # user fixed Gemini's guess
        ),
    )
    assert resp.status_code == 200

    row = _merchant_category_row(user_a, merchant)
    assert row is not None, "override at confirm time did not seed merchant_category"
    assert row["category"] == "Coffee Shops"


def test_confirm_without_override_does_not_touch_merchant_category(
    client, user_a, card_a
):
    """Verify that confirm without override does not touch merchant category."""
    merchant = f"ConfirmShop-{_tag()}"
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=merchant,
            card_id=card_a,
            gemini_suggestion="Groceries",
            category="Groceries",  # user kept the guess
        ),
    )
    assert resp.status_code == 200
    assert _merchant_category_row(user_a, merchant) is None, (
        "pure confirmations should not pollute merchant_category (DESIGN.md §8.4)"
    )


def test_confirm_without_gemini_suggestion_does_not_upsert(client, user_a, card_a):
    """No baseline to compare against — don't seed the cache speculatively."""
    merchant = f"NoSuggestionShop-{_tag()}"
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=merchant,
            card_id=card_a,
            gemini_suggestion=None,
            category="Dining",
        ),
    )
    assert resp.status_code == 200
    assert _merchant_category_row(user_a, merchant) is None


# ---------------------------------------------------------------------------
# POST /transactions/confirm — client_request_id idempotency
# ---------------------------------------------------------------------------


def test_confirm_replay_returns_existing_row_without_duplicating(
    client, user_a, card_a
):
    """Verify that confirm replay returns existing row without duplicating."""
    merchant = f"ReplayShop-{_tag()}"
    crid = str(uuid.uuid4())
    payload = _proposal(
        merchant=merchant, card_id=card_a, client_request_id=crid
    )

    first = client.post(
        "/transactions/confirm", headers=_auth(user_a), json=payload
    )
    assert first.status_code == 200
    first_id = first.json()["transaction"]["id"]

    second = client.post(
        "/transactions/confirm", headers=_auth(user_a), json=payload
    )
    assert second.status_code == 200
    assert second.json()["transaction"]["id"] == first_id
    assert second.json()["insight"] is None

    # And only one row exists in the DB for this client_request_id.
    supa = supabase_for_user(user_a.jwt)
    rows = (
        supa.table("transactions")
        .select("id")
        .eq("client_request_id", crid)
        .execute()
    )
    assert len(rows.data) == 1


# ---------------------------------------------------------------------------
# PATCH /transactions/{id} — merchant_category upsert rules
# ---------------------------------------------------------------------------


def test_patch_category_upserts_merchant_category(client, user_a, card_a):
    """Verify that patch category upserts merchant category."""
    merchant = f"PatchShop-{_tag()}"
    tx_id = _confirm_and_return_id(
        client, user_a, card_a,
        merchant=merchant, gemini_suggestion="Groceries", category="Groceries",
    )

    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"category": "Dining"},
    )
    assert resp.status_code == 200
    row = _merchant_category_row(user_a, merchant)
    assert row is not None and row["category"] == "Dining"
    first_updated_at = row["updated_at"]

    # Second PATCH to a different category — same merchant_category row
    # updated (not a new row), updated_at advances.
    resp2 = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"category": "Coffee Shops"},
    )
    assert resp2.status_code == 200
    row2 = _merchant_category_row(user_a, merchant)
    assert row2 is not None and row2["category"] == "Coffee Shops"
    assert row2["id"] == row["id"], "expected same row updated, not a new insert"
    assert row2["updated_at"] > first_updated_at


def test_patch_merchant_only_does_not_touch_merchant_category(client, user_a, card_a):
    """Verify that patch merchant only does not touch merchant category."""
    original = f"MerchantPatch-{_tag()}"
    tx_id = _confirm_and_return_id(
        client, user_a, card_a,
        merchant=original, gemini_suggestion="Groceries", category="Groceries",
    )
    # Precondition: no merchant_category row yet (confirmation, not override).
    assert _merchant_category_row(user_a, original) is None

    renamed = f"{original}-Renamed"
    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"merchant": renamed},
    )
    assert resp.status_code == 200
    # Neither old nor new name should have a merchant_category row — merchant-
    # only edits aren't corrections.
    assert _merchant_category_row(user_a, original) is None
    assert _merchant_category_row(user_a, renamed) is None


def test_patch_merchant_and_category_keys_upsert_on_new_merchant(
    client, user_a, card_a
):
    """Verify that patch merchant and category keys upsert on new merchant."""
    original = f"BothPatch-{_tag()}"
    tx_id = _confirm_and_return_id(
        client, user_a, card_a,
        merchant=original, gemini_suggestion="Groceries", category="Groceries",
    )

    renamed = f"{original}-Fixed"
    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"merchant": renamed, "category": "Coffee Shops"},
    )
    assert resp.status_code == 200

    assert _merchant_category_row(user_a, original) is None, (
        "upsert should not have used the original merchant name"
    )
    new_row = _merchant_category_row(user_a, renamed)
    assert new_row is not None and new_row["category"] == "Coffee Shops"


def test_patch_clears_card_id_on_explicit_null(client, user_a, card_a):
    """A PATCH body with `card_id: null` should clear the FK, not be
    treated as 'field omitted.' (P2 fix.)"""
    merchant = f"ClearCard-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"card_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["card_id"] is None


def test_patch_clears_notes_on_explicit_null(client, user_a, card_a):
    """Verify that patch clears notes on explicit null."""
    merchant = f"ClearNotes-{_tag()}"
    tx_id = _confirm_and_return_id(
        client, user_a, card_a, merchant=merchant, notes="original note"
    )
    # Sanity check: notes present after confirm.
    initial = client.get(f"/transactions/{tx_id}", headers=_auth(user_a)).json()
    assert initial["notes"] == "original note"

    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"notes": None},
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] is None


def test_patch_rejects_explicit_null_on_not_null_column(client, user_a, card_a):
    """NOT NULL columns (merchant, amount, date, category) must 422
    instead of cascading into a Postgres constraint violation."""
    merchant = f"NullReq-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    for field in ("merchant", "amount", "date", "category"):
        resp = client.patch(
            f"/transactions/{tx_id}",
            headers=_auth(user_a),
            json={field: None},
        )
        assert resp.status_code == 422, f"{field}: {resp.text}"
        assert resp.json()["detail"]["code"] == "null_not_allowed"


def test_patch_omitted_field_leaves_stored_value_alone(client, user_a, card_a):
    """Omitted keys must not be clobbered even when we're using
    model_fields_set — the PATCH semantics should still be 'touch what's
    sent, leave the rest.'"""
    merchant = f"PartialPatch-{_tag()}"
    tx_id = _confirm_and_return_id(
        client, user_a, card_a, merchant=merchant, notes="keep me"
    )

    # PATCH something unrelated — notes should still be there.
    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"amount": "999.99"},
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "keep me"
    assert str(resp.json()["amount"]) == "999.99"


def test_patch_same_category_as_stored_does_not_upsert(client, user_a, card_a):
    """Verify that patch same category as stored does not upsert."""
    merchant = f"NoopPatch-{_tag()}"
    tx_id = _confirm_and_return_id(
        client, user_a, card_a,
        merchant=merchant, gemini_suggestion="Groceries", category="Groceries",
    )
    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_a),
        json={"category": "Groceries"},  # unchanged
    )
    assert resp.status_code == 200
    assert _merchant_category_row(user_a, merchant) is None


# ---------------------------------------------------------------------------
# GET /transactions — filters, ordering, pagination, limit clamp
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_transactions(client, user_a, card_a):
    """Seed a small set of transactions the GET tests filter against.

    Merchant tag + date offsets are fresh per test session so we don't
    collide with leftovers."""
    tag = _tag()
    entries = [
        # (merchant, amount, days_ago, category)
        (f"Alpha-{tag}", "10.00", 0, "Groceries"),
        (f"Alpha-{tag}", "15.00", 1, "Groceries"),
        (f"Beta-{tag}", "7.50", 2, "Coffee Shops"),
        (f"Gamma-{tag}", "42.00", 7, "Dining"),
        (f"Gamma-{tag}", "55.00", 30, "Dining"),
    ]
    for merchant, amount, days_ago, category in entries:
        client.post(
            "/transactions/confirm",
            headers=_auth(user_a),
            json=_proposal(
                merchant=merchant,
                amount=amount,
                txn_date=date.today() - timedelta(days=days_ago),
                card_id=card_a,
                category=category,
                gemini_suggestion=category,  # no override, no cache pollution
            ),
        )
    return tag


def test_get_filter_merchant_contains(client, user_a, seeded_transactions):
    """Verify that get filter merchant contains."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"merchant_contains": f"Alpha-{tag}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(f"Alpha-{tag}" in it["merchant"] for it in items)
    assert len(items) == 2


def test_get_filter_category(client, user_a, seeded_transactions):
    """Verify that get filter category."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"category": "Coffee Shops", "merchant_contains": tag},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["category"] == "Coffee Shops"


def test_get_filter_amount_bounds(client, user_a, seeded_transactions):
    """Verify that get filter amount bounds."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={
            "amount_min": 10,
            "amount_max": 20,
            "merchant_contains": tag,
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    amounts = [float(it["amount"]) for it in items]
    assert amounts, "expected at least one row in [10, 20]"
    assert all(10 <= a <= 20 for a in amounts)


def test_get_filter_date_range(client, user_a, seeded_transactions):
    """Verify that get filter date range."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={
            "date_from": (date.today() - timedelta(days=3)).isoformat(),
            "date_to": date.today().isoformat(),
            "merchant_contains": tag,
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Alpha x2 + Beta x1 (days_ago 0, 1, 2) — the 7d and 30d rows are out.
    assert len(items) == 3


def test_get_is_ordered_by_date_desc(client, user_a, seeded_transactions):
    """Verify that get is ordered by date desc."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"merchant_contains": tag},
    )
    dates = [it["date"] for it in resp.json()["items"]]
    assert dates == sorted(dates, reverse=True)


def test_get_offset_past_end_returns_empty_without_more(
    client, user_a, seeded_transactions
):
    """Verify that get offset past end returns empty without more."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"merchant_contains": tag, "offset": 10_000},
    )
    body = resp.json()
    assert body["items"] == []
    assert body["has_more"] is False


def test_get_has_more_true_when_more_rows_exist(
    client, user_a, seeded_transactions
):
    """Verify that get has more true when more rows exist."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"merchant_contains": tag, "limit": 2},
    )
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True


def test_get_limit_silently_clamps_at_service_max(
    client, user_a, seeded_transactions
):
    """Verify that get limit silently clamps at service max."""
    resp = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"limit": 10_000},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) <= 500


def test_get_response_has_no_total_field(client, user_a, seeded_transactions):
    """Verify that get response has no total field."""
    resp = client.get("/transactions", headers=_auth(user_a))
    body = resp.json()
    assert "total" not in body, (
        "Day 5 dropped `total` — the UX uses has_more only"
    )
    assert "items" in body and "has_more" in body


# ---------------------------------------------------------------------------
# Service-layer parity — list_transactions() matches the HTTP route.
# ---------------------------------------------------------------------------


def test_service_and_route_return_identical_payloads(
    client, user_a, seeded_transactions
):
    """Verify that service and route return identical payloads."""
    tag = seeded_transactions
    http = client.get(
        "/transactions",
        headers=_auth(user_a),
        params={"merchant_contains": tag, "limit": 50},
    )
    assert http.status_code == 200

    service_resp = list_transactions(
        _authed_user_from_fixture(user_a),
        TransactionFilters(merchant_contains=tag, limit=50),
    )

    http_items = [(it["id"], it["merchant"], it["date"]) for it in http.json()["items"]]
    service_items = [
        (str(it.id), it.merchant, it.date.isoformat()) for it in service_resp.items
    ]
    assert http_items == service_items
    assert http.json()["has_more"] == service_resp.has_more


# ---------------------------------------------------------------------------
# DELETE /transactions/{id}
# ---------------------------------------------------------------------------


def test_delete_removes_row(client, user_a, card_a):
    """Verify that DELETE soft-deletes — GET 404s but the base row survives.

    Per DESIGN.md §8.2, transactions are never hard-deleted by application
    handlers. The API surface treats deleted rows as 404 (reads go through
    the `active_transactions` view), but the underlying row is preserved
    with `status='deleted' + deleted_at` so the chat rehydrate annotation
    can surface a `deleted.` badge on prior parse cards.
    """
    merchant = f"DeleteShop-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    resp = client.delete(f"/transactions/{tx_id}", headers=_auth(user_a))
    assert resp.status_code == 204

    # GET via the view → 404.
    after = client.get(f"/transactions/{tx_id}", headers=_auth(user_a))
    assert after.status_code == 404

    # Base table still has the row, now with status='deleted'.
    sb = supabase_for_user(user_a.jwt)
    base_row = (
        sb.table("transactions")
        .select("id, status, deleted_at")
        .eq("id", tx_id)
        .execute()
        .data
    )
    assert len(base_row) == 1, "soft-deleted row should remain in base table"
    assert base_row[0]["status"] == "deleted"
    assert base_row[0]["deleted_at"] is not None


# ---------------------------------------------------------------------------
# RLS — user B cannot reach user A's rows through the API surface.
# ---------------------------------------------------------------------------


def test_user_b_cannot_get_user_a_transaction(client, user_a, user_b, card_a):
    """Verify that user b cannot get user a transaction."""
    merchant = f"PrivateShop-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    resp = client.get(f"/transactions/{tx_id}", headers=_auth(user_b))
    assert resp.status_code == 404


def test_user_b_cannot_patch_user_a_transaction(client, user_a, user_b, card_a):
    """Verify that user b cannot patch user a transaction."""
    merchant = f"PatchPrivate-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    resp = client.patch(
        f"/transactions/{tx_id}",
        headers=_auth(user_b),
        json={"category": "Dining"},
    )
    # RLS returns no matching row — handler treats as 404.
    assert resp.status_code == 404

    # User A's transaction is untouched.
    a_row = client.get(f"/transactions/{tx_id}", headers=_auth(user_a)).json()
    assert a_row["category"] != "Dining"


def test_user_b_cannot_delete_user_a_transaction(client, user_a, user_b, card_a):
    """Verify that user b cannot delete user a transaction."""
    merchant = f"DeletePrivate-{_tag()}"
    tx_id = _confirm_and_return_id(client, user_a, card_a, merchant=merchant)

    # DELETE from user B: 204 either way (to avoid id enumeration), but
    # user A's row must still be readable.
    resp = client.delete(f"/transactions/{tx_id}", headers=_auth(user_b))
    assert resp.status_code == 204

    a_row = client.get(f"/transactions/{tx_id}", headers=_auth(user_a))
    assert a_row.status_code == 200, (
        "RLS failed: user B's DELETE removed user A's row"
    )


def test_user_b_list_does_not_include_user_a_rows(
    client, user_a, user_b, seeded_transactions
):
    """Verify that user b list does not include user a rows."""
    tag = seeded_transactions
    resp = client.get(
        "/transactions",
        headers=_auth(user_b),
        params={"merchant_contains": tag},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# POST /transactions/confirm — entry-moment insight (Day 13)
# ---------------------------------------------------------------------------


def test_confirm_first_in_category_returns_null_insight(client, user_a, card_a):
    """First transaction in a category cannot trip any rule — insight stays null."""
    _wipe_entry_moment_fires(user_a)
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=f"FirstShop-{_tag()}",
            card_id=card_a,
            # Use a category we don't seed anywhere else in this suite so
            # the "first in category" guard truly fires.
            category="Health",
            gemini_suggestion="Health",
        ),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["insight"] is None


def test_confirm_fires_single_tx_notable_when_new_monthly_max(
    client, user_a, card_a
):
    """Rule 1 fires when this txn is the new monthly high in its category."""
    _wipe_entry_moment_fires(user_a)
    category = "Entertainment"
    # Seed 3 prior in-month rows below the new max so rule 1's
    # min-month-count guard clears and the new amount is a strict max.
    today = date.today()
    for _ in range(3):
        _seed_manual_transaction(
            user_a, card_a,
            merchant=f"Concert-{_tag()}",
            amount=Decimal("20"),
            category=category,
            txn_date=today,
        )

    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(
            merchant=f"NewMax-{_tag()}",
            amount="250",
            card_id=card_a,
            category=category,
            gemini_suggestion=category,
        ),
    )
    assert resp.status_code == 200, resp.text
    insight = resp.json()["insight"]
    assert insight is not None
    assert "highest single" in insight.lower()
    assert category.lower() in insight.lower()

    # Rate-limit row recorded.
    fires = _read_fires(user_a, category=category, rule_id="single_tx_notable")
    assert len(fires) == 1


def test_confirm_replay_does_not_write_entry_moment_fire(client, user_a, card_a):
    """Idempotent replay returns insight=null AND skips the rate-limit write."""
    _wipe_entry_moment_fires(user_a)
    category = "Drugstores"
    today = date.today()
    for _ in range(3):
        _seed_manual_transaction(
            user_a, card_a,
            merchant=f"Pharmacy-{_tag()}",
            amount=Decimal("8"),
            category=category,
            txn_date=today,
        )

    crid = str(uuid.uuid4())
    payload = _proposal(
        merchant=f"BigRx-{_tag()}",
        amount="120",
        card_id=card_a,
        category=category,
        gemini_suggestion=category,
        client_request_id=crid,
    )

    first = client.post("/transactions/confirm", headers=_auth(user_a), json=payload)
    assert first.status_code == 200
    assert first.json()["insight"] is not None
    fires_after_first = _read_fires(user_a, category=category, rule_id="single_tx_notable")

    second = client.post("/transactions/confirm", headers=_auth(user_a), json=payload)
    assert second.status_code == 200
    assert second.json()["insight"] is None
    # Replay must not write an additional fire row.
    fires_after_replay = _read_fires(user_a, category=category, rule_id="single_tx_notable")
    assert len(fires_after_replay) == len(fires_after_first)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _auth(user) -> dict[str, str]:
    # Day 7 — every authenticated route except /me and /auth/* runs through
    # `get_current_user_with_device`, which 401s without `X-Device-Id`. The
    # session-scoped fixtures pre-populate `device_id`.
    """Support auth."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }

def _tag() -> str:
    """Support tag."""
    return uuid.uuid4().hex[:8]

def _proposal(
    *,
    merchant: str,
    amount: str = "12.34",
    txn_date: date | None = None,
    card_id: str | None = None,
    category: str = "Groceries",
    gemini_suggestion: str | None = "Groceries",
    notes: str | None = None,
    client_request_id: str | None = None,
) -> dict:
    """Support proposal."""
    body = {
        "merchant": merchant,
        "amount": amount,
        "date": (txn_date or date.today()).isoformat(),
        "category": category,
        "client_request_id": client_request_id or str(uuid.uuid4()),
    }
    if card_id is not None:
        body["card_id"] = card_id
    if gemini_suggestion is not None:
        body["gemini_suggestion"] = gemini_suggestion
    if notes is not None:
        body["notes"] = notes
    return body

def _authed_user_from_fixture(u) -> AuthedUser:
    """Build the AuthedUser that `list_transactions()` expects without going
    through JWKS verification — the JWT is already known-valid from the
    session fixture that minted it."""
    return AuthedUser(jwt=u.jwt, user_id=uuid.UUID(u.id), email=u.email)

def _merchant_category_row(user, merchant: str) -> dict | None:
    """Support merchant category row."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("merchant_category")
        .select("*")
        .eq("merchant", normalize_merchant(merchant))
        .execute()
    )
    return resp.data[0] if resp.data else None

def _confirm_and_return_id(client, user_a, card_a, *, merchant, **kwargs) -> str:
    """Support confirm and return id."""
    resp = client.post(
        "/transactions/confirm",
        headers=_auth(user_a),
        json=_proposal(merchant=merchant, card_id=card_a, **kwargs),
    )
    assert resp.status_code == 200
    return resp.json()["transaction"]["id"]


def _seed_manual_transaction(
    user, card_id: str, *, merchant: str, amount: Decimal, category: str, txn_date: date
) -> None:
    """Insert a row directly via the user's JWT so the entry-moment RPC sees prior history.

    Bypasses the API layer because the API layer is what we're testing —
    seeding via /transactions/confirm would itself fire insights and
    record rate-limit rows, polluting the very table the tests are
    asserting on.
    """
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


def _wipe_entry_moment_fires(user) -> None:
    """Clear the user's rate-limit fires so insight tests start from a clean slate.

    The table is audit-immutable in production (no DELETE policy for the
    user), so we use a service-role admin client. This is the only safe
    place to bypass that policy — production callers never touch it.
    """
    import os
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    service_role_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    admin = create_client(url, service_role_key)
    admin.table("entry_moment_fires").delete().eq("user_id", user.id).execute()


def _read_fires(user, *, category: str, rule_id: str) -> list[dict]:
    """Return entry_moment_fires rows matching (user, category, rule_id) — RLS-scoped."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("entry_moment_fires")
        .select("*")
        .eq("user_id", user.id)
        .eq("category", category)
        .eq("rule_id", rule_id)
        .execute()
    )
    return resp.data or []
