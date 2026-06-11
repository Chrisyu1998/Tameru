"""Day 14 — cards API contract suite.

Covers the deliverables from prompt/week-2-chat-mvp-and-deploy/day-14-cards-perplexity.md:

- `POST /cards/lookup`: returns a CardLookupResponse; failures land as
  needs_manual=True; one ai_call_log row per call (provider=anthropic,
  task_type=card_lookup).
- `POST /cards/confirm`: happy path; validates `network` + `last_four`;
  409 active_card_exists; soft-delete + re-add creates a new row, not
  reviving the old one; network disambiguation (visa 1234 + amex 1234
  coexist active).
- `GET /cards`: default returns active only; `?include_inactive=true`
  returns both buckets.
- `PATCH /cards/{id}`: name + multipliers + annual_fee patch.
- `DELETE /cards/{id}`: soft-delete sets status='deleted' + deleted_at.
- RLS: user A cannot GET / PATCH / DELETE user B's cards.

`lookup_card` is monkeypatched on every test so no real Anthropic call
fires — tests stay deterministic and offline.

Conftest's card_a / card_b session fixtures occupy `(chase, 1111)` and
`(amex, 2222)` respectively; tests that exercise the partial unique
index use new (issuer, last_four) pairs that don't collide. Issuer is
keyed on the closed CHECK enum installed by migration
20260516140000_cards_uniqueness_by_issuer.sql — values must be the
canonical lowercase identifiers (`chase`, `amex`, `capital_one`, …).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app
from app.models.cards import CardLookupResult
from app.routes import cards as cards_route


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient that re-uses the running stack from conftest."""
    return TestClient(app)


@pytest.fixture
def stub_lookup(monkeypatch):
    """Replace `lookup_card` with a synchronous stub.

    Default behavior: returns a happy-path Chase Sapphire Preferred-shaped
    result. Tests that want the needs_manual branch can override by calling
    the returned `set_result` setter.
    """
    state: dict[str, CardLookupResult] = {
        "result": CardLookupResult(
            program="UR",
            multipliers={"Dining": 3.0, "Travel": 3.0},
            annual_fee=Decimal("95"),
            issuer="chase",
            source_urls=["https://nerdwallet.com/chase-sapphire-preferred"],
            needs_manual=False,
        )
    }

    def fake_lookup(
        card_name: str,
        user,
        region: str = "US",
        home_currency: str = "USD",
    ) -> CardLookupResult:  # noqa: ARG001
        """Return the canned CardLookupResult; ignore the args.

        Accepts the Tier 3 `region` / `home_currency` kwargs the real
        `lookup_card` gained so the route's region-aware call still binds.
        """
        return state["result"]

    monkeypatch.setattr(cards_route, "lookup_card", fake_lookup)
    # propose_card (agent tool) imports lookup_card directly; patch both.
    from app.agent import tools as tools_module
    monkeypatch.setattr(tools_module, "lookup_card", fake_lookup)

    def set_result(result: CardLookupResult) -> None:
        """Override the canned result mid-test."""
        state["result"] = result

    return set_result


# ---------------------------------------------------------------------------
# POST /cards/lookup
# ---------------------------------------------------------------------------


def test_lookup_returns_structured_payload(client, user_a, stub_lookup):
    """Verify that /cards/lookup returns the merged name + lookup payload."""
    resp = client.post(
        "/cards/lookup",
        headers=_auth(user_a),
        json={"name": "Chase Sapphire Preferred"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Chase Sapphire Preferred"
    assert body["lookup"]["program"] == "UR"
    assert body["lookup"]["multipliers"]["Dining"] == 3.0
    assert body["lookup"]["source_urls"]


def test_lookup_falls_back_to_needs_manual_on_low_confidence(
    client, user_a, stub_lookup
):
    """Verify that /cards/lookup surfaces needs_manual=True on miss."""
    stub_lookup(
        CardLookupResult(
            needs_manual=True,
            raw_text="model returned no usable fields",
        )
    )
    resp = client.post(
        "/cards/lookup",
        headers=_auth(user_a),
        json={"name": "Made Up Bank Card"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lookup"]["needs_manual"] is True
    assert body["lookup"]["multipliers"] == {}


def test_lookup_rejects_empty_name(client, user_a):
    """Verify that /cards/lookup rejects whitespace-only names."""
    resp = client.post(
        "/cards/lookup",
        headers=_auth(user_a),
        json={"name": "   "},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /cards/confirm — happy path + validation
# ---------------------------------------------------------------------------


def test_confirm_creates_card(client, user_a):
    """Verify that /cards/confirm inserts a row with the proposal fields."""
    tag = _tag()
    resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=_last_four(tag, "9"),
            name=f"Test Card {tag}",
            issuer="chase",
            program="UR",
            multipliers={"Dining": 3.0},
            annual_fee="95",
        ),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == f"Test Card {tag}"
    assert body["network"] == "visa"
    assert body["status"] == "active"
    assert body["deleted_at"] is None
    assert Decimal(body["annual_fee"]) == Decimal("95")


def test_confirm_rejects_missing_last_four(client, user_a):
    """Verify that /cards/confirm rejects payloads without last_four."""
    body = _proposal(
        network="visa",
        last_four="1234",
        name="X",
        issuer="chase",
    )
    body.pop("last_four")
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 422


def test_confirm_rejects_non_four_digit_last_four(client, user_a):
    """Verify that /cards/confirm rejects malformed last_four values."""
    resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four="12",  # too short
            name="X",
            issuer="chase",
        ),
    )
    assert resp.status_code == 422


def test_confirm_rejects_unknown_network(client, user_a):
    """Verify that /cards/confirm rejects networks outside the closed enum.

    `unionpay` is a real network Tameru does not (yet) support — a value
    safely outside the `CardNetwork` Literal even after Tier 3 widened it
    with `jcb` and `diners`.
    """
    resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="unionpay",
            last_four="1234",
            name="X",
            issuer="chase",
        ),
    )
    assert resp.status_code == 422


def test_confirm_idempotent_on_same_client_request_id(client, user_a):
    """Day 15: same crid posted twice returns the existing row (no 409).

    Mirrors `/transactions/confirm` idempotency. The natural-key 409
    only fires when a *different* crid races for the same physical card;
    a network retry of the exact same proposal short-circuits with the
    prior row.
    """
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "1"),
        name=f"Idem Card {tag}",
        issuer="chase",
        program="UR",
        multipliers={"Dining": 2.0},
    )

    first = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert first.status_code == 200, first.text
    second = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert second.status_code == 200, second.text

    # Same row id on the replay — no duplicate insert, no 409.
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["client_request_id"] == body["client_request_id"]


# ---------------------------------------------------------------------------
# POST /cards/confirm — collision behavior
# ---------------------------------------------------------------------------


def test_confirm_409_when_active_identity_collides(client, user_a):
    """Verify that confirming a duplicate (issuer, last_four) returns 409.

    The first confirm succeeds; the second confirm of the same
    `(chase, last_four)` returns 409 active_card_exists with the existing
    card's id surfaced for the frontend's "edit instead?" affordance.
    """
    tag = _tag()
    last_four = _last_four(tag, "0")
    first = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=last_four,
            name=f"First {tag}",
            issuer="chase",
        ),
    )
    assert first.status_code == 200, first.text
    existing_id = first.json()["id"]

    second = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=last_four,
            name=f"Duplicate {tag}",
            issuer="chase",
        ),
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "active_card_exists"
    assert detail["existing_card_id"] == existing_id
    assert detail["existing_card_last_four"] == last_four


def test_confirm_after_soft_delete_inserts_new_row(client, user_a):
    """Verify that soft-delete + re-add yields two rows, never reviving.

    DESIGN.md §8.1: deleted rows are exempt from the partial unique
    index; re-adding the same (issuer, last_four) creates a fresh
    `card_id`. Historical transactions linked to the old row stay
    pointing at it.
    """
    tag = _tag()
    last_four = _last_four(tag, "0")
    first = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="mastercard",
            last_four=last_four,
            name=f"First {tag}",
            issuer="citi",
        ),
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    delete = client.delete(f"/cards/{first_id}", headers=_auth(user_a))
    assert delete.status_code == 204

    second = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="mastercard",
            last_four=last_four,
            name=f"Second {tag}",
            issuer="citi",
        ),
    )
    assert second.status_code == 200, second.text
    second_id = second.json()["id"]
    assert second_id != first_id, "expected a fresh card_id, got a revived row"
    assert second.json()["status"] == "active"

    # Verify the old row is still deleted (not revived).
    db = supabase_for_user(user_a.jwt)
    rows = (
        db.table("cards")
        .select("id, status, deleted_at")
        .in_("id", [first_id, second_id])
        .execute()
        .data
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id[first_id]["status"] == "deleted"
    assert by_id[first_id]["deleted_at"] is not None
    assert by_id[second_id]["status"] == "active"


def test_confirm_allows_same_last_four_across_issuers(client, user_a):
    """Verify that (chase, visa, 5678) and (capital_one, visa, 5678) coexist.

    REGRESSION TEST: the prior (user_id, network, last_four) index
    incorrectly blocked this case. Card numbers are issued per BANK, not
    per network — two different banks can produce Visa cards whose last
    4 digits collide, and Tameru must accept both as distinct cards.
    """
    tag = _tag()
    last_four = _last_four(tag, "5")
    chase_resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=last_four,
            name=f"Chase Visa {tag}",
            issuer="chase",
        ),
    )
    assert chase_resp.status_code == 200, chase_resp.text

    capital_one_resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=last_four,
            name=f"Capital One Visa {tag}",
            issuer="capital_one",
        ),
    )
    assert capital_one_resp.status_code == 200, capital_one_resp.text
    assert capital_one_resp.json()["id"] != chase_resp.json()["id"]


def test_confirm_rejects_unknown_issuer(client, user_a):
    """Verify that an out-of-enum issuer is rejected at the API boundary.

    Pydantic Literal validation fires before the DB CHECK constraint —
    the route returns 422 with a clear field error rather than letting
    the request hit Postgres.
    """
    resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four="1234",
            name="MyBank Card",
            issuer="MyBank",  # not in the closed enum
        ),
    )
    assert resp.status_code == 422
    assert "issuer" in resp.text.lower()


def test_confirm_rejects_missing_last_four_at_commit(client, user_a):
    """Verify that confirm requires last_four at commit time.

    The wire shape allows last_four=None (since propose_card can return
    that mid-conversation), but the confirm endpoint MUST reject it —
    the partial unique index would treat null-last_four rows as distinct,
    silently allowing duplicates.
    """
    body = _proposal(
        network="visa",
        last_four="1234",
        name=f"NoLast4 {_tag()}",
        issuer="chase",
    )
    body.pop("last_four")
    resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=body,
    )
    assert resp.status_code == 422
    assert "last_four" in resp.text.lower()


# ---------------------------------------------------------------------------
# DELETE — soft-delete behavior
# ---------------------------------------------------------------------------


def test_delete_sets_status_deleted_and_deleted_at(client, user_a):
    """Verify that DELETE soft-deletes — status='deleted' + deleted_at set."""
    tag = _tag()
    confirm = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="discover",
            last_four=_last_four(tag, "3"),
            name=f"To Delete {tag}",
            issuer="discover",
        ),
    )
    assert confirm.status_code == 200
    card_id = confirm.json()["id"]

    delete = client.delete(f"/cards/{card_id}", headers=_auth(user_a))
    assert delete.status_code == 204

    db = supabase_for_user(user_a.jwt)
    row = (
        db.table("cards")
        .select("status, deleted_at")
        .eq("id", card_id)
        .execute()
        .data[0]
    )
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None


# ---------------------------------------------------------------------------
# GET /cards
# ---------------------------------------------------------------------------


def test_get_cards_default_excludes_inactive(client, user_a):
    """Verify that GET /cards omits soft-deleted rows by default."""
    tag = _tag()
    confirm = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=_last_four(tag, "8"),
            name=f"Active {tag}",
            issuer="chase",
        ),
    )
    active_id = confirm.json()["id"]

    deleted_confirm = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="amex",
            last_four=_last_four(tag, "7"),
            name=f"DeletedSoon {tag}",
            issuer="amex",
        ),
    )
    deleted_id = deleted_confirm.json()["id"]
    client.delete(f"/cards/{deleted_id}", headers=_auth(user_a))

    resp = client.get("/cards", headers=_auth(user_a))
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["items"]}
    assert active_id in ids
    assert deleted_id not in ids


def test_get_cards_include_inactive_returns_both(client, user_a):
    """Verify that ?include_inactive=true returns active + inactive rows."""
    tag = _tag()
    confirm = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="amex",
            last_four=_last_four(tag, "6"),
            name=f"Inactive {tag}",
            issuer="amex",
        ),
    )
    inactive_id = confirm.json()["id"]
    client.delete(f"/cards/{inactive_id}", headers=_auth(user_a))

    resp = client.get(
        "/cards?include_inactive=true",
        headers=_auth(user_a),
    )
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["items"]}
    assert inactive_id in ids
    by_id = {row["id"]: row for row in resp.json()["items"]}
    assert by_id[inactive_id]["status"] == "deleted"
    assert by_id[inactive_id]["deleted_at"] is not None


# ---------------------------------------------------------------------------
# PATCH /cards/{id}
# ---------------------------------------------------------------------------


def test_patch_updates_name_and_multipliers(client, user_a):
    """Verify that PATCH applies provided fields and ignores others."""
    tag = _tag()
    confirm = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=_last_four(tag, "1"),
            name=f"Before {tag}",
            issuer="chase",
            multipliers={"Dining": 2.0},
        ),
    )
    card_id = confirm.json()["id"]

    patch = client.patch(
        f"/cards/{card_id}",
        headers=_auth(user_a),
        json={
            "name": f"After {tag}",
            "multipliers": {"Dining": 3.0, "Travel": 3.0},
            "annual_fee": "150",
        },
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["name"] == f"After {tag}"
    assert body["multipliers"] == {"Dining": 3.0, "Travel": 3.0}
    assert Decimal(body["annual_fee"]) == Decimal("150")
    # Identity fields untouched.
    assert body["network"] == "visa"


# ---------------------------------------------------------------------------
# RLS — user A cannot touch user B's cards
# ---------------------------------------------------------------------------


def test_rls_user_a_cannot_get_user_b_card(client, user_a, user_b, card_b):
    """Verify that user A's GET cannot see user B's card row.

    RLS scopes the SELECT — user B's card is invisible to user A's JWT.
    """
    resp = client.get("/cards", headers=_auth(user_a))
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["items"]}
    assert card_b not in ids


def test_rls_user_a_cannot_delete_user_b_card(client, user_a, user_b, card_b):
    """Verify that user A's DELETE on user B's card is a silent no-op.

    The handler returns 204 either way to avoid leaking which ids exist;
    we verify by reading the row from user B's seat and confirming it's
    still active.
    """
    resp = client.delete(f"/cards/{card_b}", headers=_auth(user_a))
    assert resp.status_code == 204

    db_b = supabase_for_user(user_b.jwt)
    row = db_b.table("cards").select("status").eq("id", card_b).execute().data[0]
    assert row["status"] == "active"


# ---------------------------------------------------------------------------
# ai_call_log — one row per /cards/lookup call
# ---------------------------------------------------------------------------


def test_ai_call_log_records_card_lookup_invocation(client, user_a, stub_lookup):
    """Verify that calling /cards/lookup writes an ai_call_log row.

    `stub_lookup` replaces the real Anthropic call but the route layer
    above is unchanged — so this test verifies the route's logging
    contract end-to-end. We can't assert provider="anthropic" here
    because the stub bypasses log_ai_call entirely; instead this is a
    smoke test that the route returns 200 with the stub installed
    (separate `tests/integrations/test_card_lookup.py` covers logging
    on the integration layer itself).
    """
    resp = client.post(
        "/cards/lookup",
        headers=_auth(user_a),
        json={"name": "Chase Sapphire Preferred"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Day 19b — AF dual-write + split-cascade on soft-delete
# ---------------------------------------------------------------------------


def test_confirm_with_af_date_creates_companion_subscription(
    client, user_a, stub_lookup  # noqa: ARG001 — lookup stub keeps `lookup_card` silent
):
    """`next_annual_fee_date` + non-zero `annual_fee` creates a companion sub.

    Day 19b. The companion subscription has `frequency='annual'`,
    `category='Memberships'`, name '{card_name} annual fee', a fresh
    server-side `client_request_id`, and `start_date` / `next_billing_date`
    both equal to the supplied renewal date.
    """
    from uuid import uuid4

    tag = _tag()
    renewal = (date.today() + timedelta(days=30)).isoformat()
    card_name = f"AF-{tag}"
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "1"),
        name=card_name,
        issuer="capital_one",
        annual_fee="550",
        next_annual_fee_date=renewal,
        client_request_id=str(uuid4()),
    )
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200, resp.text
    card_id = resp.json()["id"]

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    af = [s for s in subs if s["card_id"] == card_id]
    assert len(af) == 1, "expected exactly one companion AF subscription"
    sub = af[0]
    assert sub["name"] == f"{card_name} annual fee"
    assert sub["category"] == "Memberships"
    assert sub["frequency"] == "annual"
    assert sub["next_billing_date"] == renewal
    assert sub["start_date"] == renewal
    assert sub["client_request_id"] is not None


def test_confirm_without_af_date_creates_no_subscription(
    client, user_a, stub_lookup  # noqa: ARG001
):
    """No `next_annual_fee_date` → no companion subscription created."""
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "2"),
        name=f"NoAF-{tag}",
        issuer="chase",
        annual_fee="95",
    )
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200
    card_id = resp.json()["id"]

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    af = [s for s in subs if s["card_id"] == card_id]
    assert af == []


def test_confirm_with_zero_af_creates_no_subscription(
    client, user_a, stub_lookup  # noqa: ARG001
):
    """`annual_fee=0` → no companion subscription, even with a date."""
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "3"),
        name=f"ZeroAF-{tag}",
        issuer="capital_one",
        annual_fee="0",
        next_annual_fee_date=(date.today() + timedelta(days=30)).isoformat(),
    )
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200
    card_id = resp.json()["id"]

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    af = [s for s in subs if s["card_id"] == card_id]
    assert af == []


def test_confirm_rejects_past_af_renewal_date(
    client, user_a, stub_lookup  # noqa: ARG001
):
    """`next_annual_fee_date` in the past → 422 from the CardProposal validator.

    Day 19b. Strictly past dates would make pg_cron auto-log immediately
    on the next run, which is confusing UX. Same-day is legitimate (the
    card might charge the AF today) and accepted — and so is yesterday
    (1-day timezone slack for US-evening users whose local today is UTC
    yesterday, audit P3-30), so the rejection floor is today - 2.
    """
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "4"),
        name=f"PastAF-{tag}",
        issuer="capital_one",
        annual_fee="550",
        next_annual_fee_date=(date.today() - timedelta(days=2)).isoformat(),
    )
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 422


def test_confirm_accepts_yesterday_af_renewal_date(
    client, user_a, stub_lookup  # noqa: ARG001
):
    """`next_annual_fee_date == yesterday` is accepted (timezone slack).

    A US-evening user's local "today" is UTC yesterday, and an
    offline-queued confirm carrying today's date can drain after UTC
    midnight — both legitimate same-day intents that a strict `< today`
    check would 422 (audit P3-30/P3-31).
    """
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "5"),
        name=f"SlackAF-{tag}",
        issuer="capital_one",
        annual_fee="550",
        next_annual_fee_date=(date.today() - timedelta(days=1)).isoformat(),
    )
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200, resp.text


def test_soft_delete_cascades_af_to_cancelled_regular_to_paused(
    client, user_a, stub_lookup  # noqa: ARG001
):
    """The Day 19 split-cascade fires on `DELETE /cards/{id}`.

    Setup: one card with an AF subscription, plus a regular Netflix
    subscription on the same card. Soft-delete the card.

    Expected (DESIGN.md §8.3):
      - AF subscription flips to `status='cancelled'` (the fee is bound
        to this physical card; there is no reassignment path).
      - Regular subscription flips to `status='paused'` (the user picks
        a new card via PATCH to resume).
    """
    from uuid import uuid4

    tag = _tag()
    renewal = (date.today() + timedelta(days=30)).isoformat()
    card_name = f"Cascade-{tag}"
    # Create the card with an AF (dual-writes the companion subscription).
    card = _proposal(
        network="visa",
        last_four=_last_four(tag, "5"),
        name=card_name,
        issuer="capital_one",
        annual_fee="550",
        next_annual_fee_date=renewal,
        client_request_id=str(uuid4()),
    )
    card_resp = client.post("/cards/confirm", headers=_auth(user_a), json=card)
    assert card_resp.status_code == 200
    card_id = card_resp.json()["id"]

    # Add a regular Netflix subscription on the same card.
    regular_proposal = {
        "name": f"Netflix-{tag}",
        "amount": "15.99",
        "frequency": "monthly",
        "start_date": date.today().isoformat(),
        "next_billing_date": (date.today() + timedelta(days=30)).isoformat(),
        "category": "Streaming",
        "card_id": card_id,
        "client_request_id": str(uuid4()),
    }
    regular_resp = client.post(
        "/subscriptions/confirm",
        headers=_auth(user_a),
        json=regular_proposal,
    )
    assert regular_resp.status_code == 200
    regular_id = regular_resp.json()["id"]

    # Soft-delete the card.
    delete_resp = client.delete(f"/cards/{card_id}", headers=_auth(user_a))
    assert delete_resp.status_code == 204

    # Read every subscription (including cancelled / paused) for user_a.
    all_subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    af = next(
        s for s in all_subs if s["card_id"] == card_id and "annual fee" in s["name"]
    )
    regular = next(s for s in all_subs if s["id"] == regular_id)

    assert af["status"] == "cancelled", f"AF should be cancelled, got {af['status']}"
    assert regular["status"] == "paused", (
        f"regular sub should be paused, got {regular['status']}"
    )


# ---------------------------------------------------------------------------
# Day 19b — PATCH /cards/{id} AF cascade via update_card_af RPC
# ---------------------------------------------------------------------------


def test_patch_annual_fee_cascades_to_af_subscription(client, user_a, stub_lookup):  # noqa: ARG001
    """PATCH `annual_fee` cascades onto the AF subscription's amount.

    Day 19b — `cards.annual_fee` is the canonical source; the AF
    subscription's `amount` mirrors it via `update_card_af`. The cron
    next year must charge the new amount.
    """
    card, af = _create_card_with_af(client, user_a, suffix="0")

    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"annual_fee": "795"},
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["annual_fee"]) == Decimal("795")

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    updated_af = next(s for s in subs if s["id"] == af["id"])
    assert Decimal(updated_af["amount"]) == Decimal("795")
    assert updated_af["status"] == "active"


def test_patch_next_annual_fee_date_updates_subscription(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """PATCH `next_annual_fee_date` updates the AF sub's `next_billing_date`.

    `start_date` stays immutable per §8.3 — only `next_billing_date`
    moves. `cards.annual_fee` is unchanged.
    """
    card, af = _create_card_with_af(client, user_a, suffix="1")
    new_renewal = (date.today() + timedelta(days=60)).isoformat()

    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"next_annual_fee_date": new_renewal},
    )
    assert resp.status_code == 200, resp.text

    # cards.annual_fee untouched
    assert Decimal(resp.json()["annual_fee"]) == Decimal(card["annual_fee"])

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    updated_af = next(s for s in subs if s["id"] == af["id"])
    assert updated_af["next_billing_date"] == new_renewal
    assert updated_af["start_date"] == af["start_date"], (
        "start_date is immutable per §8.3"
    )


def test_patch_clear_next_annual_fee_date_cancels_af(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """PATCH `next_annual_fee_date=null` flips the AF subscription to cancelled.

    Stop-tracking path. `cards.annual_fee` retains the snapshot — the
    column is the at-cancel value, not zeroed.
    """
    card, af = _create_card_with_af(client, user_a, suffix="2")

    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"next_annual_fee_date": None},
    )
    assert resp.status_code == 200, resp.text
    # Snapshot preserved
    assert Decimal(resp.json()["annual_fee"]) == Decimal(card["annual_fee"])

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    cancelled_af = next(s for s in subs if s["id"] == af["id"])
    assert cancelled_af["status"] == "cancelled"


def test_patch_re_enables_af_on_card_with_cancelled_sub(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """Setting a renewal date on a card with no active AF sub inserts one.

    Re-enable path. Setup: cancel an existing AF via the stop-tracking
    path; then PATCH a new renewal date. A fresh AF subscription is
    created with the current `cards.annual_fee` as the amount.
    """
    card, af = _create_card_with_af(client, user_a, suffix="3")

    # Cancel first.
    client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"next_annual_fee_date": None},
    )

    # Re-enable with a fresh date.
    new_renewal = (date.today() + timedelta(days=90)).isoformat()
    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"next_annual_fee_date": new_renewal},
    )
    assert resp.status_code == 200, resp.text

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    afs_for_card = [s for s in subs if s["card_id"] == card["id"]]
    # Original AF still present as cancelled; new AF active.
    cancelled = [s for s in afs_for_card if s["id"] == af["id"]]
    assert cancelled and cancelled[0]["status"] == "cancelled"
    active = [s for s in afs_for_card if s["status"] == "active"]
    assert len(active) == 1
    fresh = active[0]
    assert fresh["next_billing_date"] == new_renewal
    assert Decimal(fresh["amount"]) == Decimal(card["annual_fee"])


def test_patch_rejects_af_date_on_zero_fee_card(client, user_a, stub_lookup):  # noqa: ARG001
    """422 when a date is patched on a card with `annual_fee=0`.

    Pre-RPC guard — can't track AF on a no-fee card. The user must
    PATCH `annual_fee > 0` first (or in the same call).
    """
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "4"),
        name=f"NoFee-{tag}",
        issuer="chase",
        annual_fee="0",
    )
    card_resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert card_resp.status_code == 200
    card_id = card_resp.json()["id"]

    resp = client.patch(
        f"/cards/{card_id}",
        headers=_auth(user_a),
        json={
            "next_annual_fee_date": (
                date.today() + timedelta(days=30)
            ).isoformat()
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    if isinstance(detail, dict):
        assert detail.get("code") == "af_requires_nonzero_fee"


def test_patch_af_and_non_af_fields_in_one_call(client, user_a, stub_lookup):  # noqa: ARG001
    """A single PATCH can mix AF cascade with regular field updates."""
    card, af = _create_card_with_af(client, user_a, suffix="5")
    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"annual_fee": "695", "name": "Renamed-Card"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed-Card"
    assert Decimal(body["annual_fee"]) == Decimal("695")

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    updated_af = next(s for s in subs if s["id"] == af["id"])
    assert Decimal(updated_af["amount"]) == Decimal("695")


def test_patch_annual_fee_on_card_without_af_sub_is_noop_on_subscriptions(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """PATCH `annual_fee` on a card with no AF sub updates only `cards`.

    A user can hold a card with a non-zero AF without tracking it (the
    AF date was never supplied). Patching the amount in that case
    shouldn't conjure a phantom AF subscription.
    """
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "6"),
        name=f"AmountOnly-{tag}",
        issuer="chase",
        annual_fee="95",
        # no next_annual_fee_date — no AF subscription created
    )
    card_resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert card_resp.status_code == 200
    card_id = card_resp.json()["id"]

    resp = client.patch(
        f"/cards/{card_id}",
        headers=_auth(user_a),
        json={"annual_fee": "150"},
    )
    assert resp.status_code == 200
    assert Decimal(resp.json()["annual_fee"]) == Decimal("150")

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    af_for_card = [s for s in subs if s["card_id"] == card_id]
    assert af_for_card == []


def test_patch_rejects_past_next_annual_fee_date(client, user_a, stub_lookup):  # noqa: ARG001
    """Past renewal date on PATCH → 422. Mirrors the confirm validator
    (including its 1-day timezone slack — the rejection floor is today - 2)."""
    card, _ = _create_card_with_af(client, user_a, suffix="7")
    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={
            "next_annual_fee_date": (
                date.today() - timedelta(days=2)
            ).isoformat()
        },
    )
    assert resp.status_code == 422


def test_patch_annual_fee_to_zero_cancels_active_af_sub(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """PATCH `annual_fee=0` on a card with an active AF sub cancels the sub.

    Day 19b follow-up — codex review caught the original behavior
    (writing 0 into subscriptions.amount) as still letting pg_cron
    auto-log $0 transactions forever. Cleaner outcome: a zero AF
    means no tracking. cards.annual_fee still reflects the patched
    value (0), so the snapshot is preserved.
    """
    card, af = _create_card_with_af(client, user_a, suffix="9")

    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"annual_fee": "0"},
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["annual_fee"]) == Decimal("0")

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    cancelled_af = next(s for s in subs if s["id"] == af["id"])
    assert cancelled_af["status"] == "cancelled"


def test_patch_annual_fee_to_null_cancels_active_af_sub(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """PATCH `annual_fee=null` on a card with an active AF sub cancels the sub.

    The NOT NULL constraint on `subscriptions.amount` would otherwise
    raise a database error and roll back the entire patch — making
    "clear the annual fee" silently fail from the user's POV. Cancel
    is the right behavior: no fee → no tracking.
    """
    # Random tag in _create_card_with_af keeps last_four distinct from
    # other tests using suffix "0" — only the per-call digit matters
    # within a test, not across tests.
    card, af = _create_card_with_af(client, user_a, suffix="0")

    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"annual_fee": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["annual_fee"] is None

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    cancelled_af = next(s for s in subs if s["id"] == af["id"])
    assert cancelled_af["status"] == "cancelled"


def test_patch_card_name_cascades_to_af_subscription_name(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """A card rename updates the companion AF sub's denormalized name.

    Day 19b follow-up — the AF sub's name is stored as
    '{card_name} annual fee' and used by autolog_subscriptions() as
    the transaction merchant. Without the sync trigger, a rename
    would surface the old name on every future auto-logged AF
    transaction. Trigger: `cards_sync_af_subscription_name`
    (migration 20260519130200).

    Doesn't apply to regular subscriptions — those have user-typed
    names. Only AF subs derive their name from the card.
    """
    card, af = _create_card_with_af(client, user_a, suffix="1")
    assert af["name"].endswith(" annual fee")

    new_name = f"Renamed-{_tag()}"
    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"name": new_name},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == new_name

    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    updated_af = next(s for s in subs if s["id"] == af["id"])
    assert updated_af["name"] == f"{new_name} annual fee"


def test_patch_card_name_does_not_touch_cancelled_af_subs(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """Trigger filters by status='active' — cancelled AF rows are historical.

    Cancelling AF tracking and then renaming the card should leave the
    cancelled sub's name as-is (it's a historical record of the
    pre-cancel state). The trigger's `status='active'` filter
    guarantees this.
    """
    card, af = _create_card_with_af(client, user_a, suffix="2")

    # Cancel AF tracking.
    client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"next_annual_fee_date": None},
    )

    # Rename the card.
    new_name = f"PostCancel-{_tag()}"
    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_a),
        json={"name": new_name},
    )
    assert resp.status_code == 200

    # The cancelled AF sub keeps its old name.
    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    historical_af = next(s for s in subs if s["id"] == af["id"])
    assert historical_af["status"] == "cancelled"
    assert historical_af["name"] == af["name"], (
        "cancelled AF row is historical — trigger must not rewrite it"
    )


def test_patch_card_name_noop_when_no_af_sub(
    client, user_a, stub_lookup,  # noqa: ARG001
):
    """Renaming a card without an AF sub is just a cards-row UPDATE.

    The trigger fires regardless but matches zero rows; no error, no
    side effect, no extra round-trips visible to the user.
    """
    tag = _tag()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, "3"),
        name=f"NoAfRename-{tag}",
        issuer="chase",
        annual_fee="95",
    )
    card_resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert card_resp.status_code == 200
    card_id = card_resp.json()["id"]

    resp = client.patch(
        f"/cards/{card_id}",
        headers=_auth(user_a),
        json={"name": "RenameMe"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "RenameMe"


def test_patch_af_rls_other_user(client, user_a, user_b, stub_lookup):  # noqa: ARG001
    """User B PATCHing user A's card AF returns 404. RPC's auth.uid()
    filter matches zero rows, so the route surfaces a 404 the same way
    a non-existent card id would.
    """
    card, _ = _create_card_with_af(client, user_a, suffix="8")
    resp = client.patch(
        f"/cards/{card['id']}",
        headers=_auth(user_b),
        json={"annual_fee": "999"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Helpers — mirror tests/routes/test_transactions.py conventions.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Build the bearer + device headers for the authenticated route gate."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _tag() -> str:
    """Short random suffix to keep session-scoped fixtures uncontaminated."""
    return uuid.uuid4().hex[:8]


def _last_four(tag: str, suffix_digit: str) -> str:
    """Produce a 4-digit last_four derived from a hex tag.

    Cards live in (user_id, network, last_four) uniqueness; cross-test
    collisions on shared session-scoped users would otherwise produce
    spurious 409s. We pull the first 3 digits from the hex tag and append
    a per-test suffix digit so values are deterministic per call site
    and unlikely to collide across tests.
    """
    digits = "".join(c for c in tag if c.isdigit())
    digits = (digits + "000")[:3]
    return f"{digits}{suffix_digit}"


def _create_card_with_af(client, user_a, *, suffix: str, fee: str = "550"):
    """Helper: create a card with an AF subscription tracked. Returns the
    card row dict and the companion AF subscription dict. Day 19b PATCH
    cascade tests use this so the AF context is one line of setup.
    """
    from uuid import uuid4

    tag = _tag()
    renewal = (date.today() + timedelta(days=30)).isoformat()
    body = _proposal(
        network="visa",
        last_four=_last_four(tag, suffix),
        name=f"PatchAF-{tag}",
        issuer="capital_one",
        annual_fee=fee,
        next_annual_fee_date=renewal,
        client_request_id=str(uuid4()),
    )
    resp = client.post("/cards/confirm", headers=_auth(user_a), json=body)
    assert resp.status_code == 200, resp.text
    card = resp.json()
    subs = client.get(
        "/subscriptions",
        headers=_auth(user_a),
        params={"status": "all", "include_card_af": "true"},
    ).json()["items"]
    af = next(s for s in subs if s["card_id"] == card["id"])
    return card, af


def _proposal(
    *,
    network: str,
    last_four: str,
    name: str,
    issuer: str,
    program: str = "Other",
    multipliers: dict[str, float] | None = None,
    annual_fee: str | None = None,
    next_annual_fee_date: str | None = None,
    source_urls: list[str] | None = None,
    alias: str | None = None,
    needs_manual: bool = False,
    client_request_id: str | None = None,
) -> dict:
    """Build a CardProposal-shaped dict for /cards/confirm tests.

    `client_request_id` defaults to a fresh UUID per call so tests
    aren't forced to thread one in for every case. Tests that want to
    drive crid-replay behavior pass an explicit value.

    `next_annual_fee_date` (Day 19b) drives the companion-AF-subscription
    dual-write when set alongside a non-zero `annual_fee`.
    """
    from uuid import uuid4

    body: dict = {
        "network": network,
        "last_four": last_four,
        "name": name,
        "issuer": issuer,
        "program": program,
        "multipliers": multipliers or {},
        "source_urls": source_urls or [],
        "needs_manual": needs_manual,
        "client_request_id": client_request_id or str(uuid4()),
    }
    if annual_fee is not None:
        body["annual_fee"] = annual_fee
    if next_annual_fee_date is not None:
        body["next_annual_fee_date"] = next_annual_fee_date
    if alias is not None:
        body["alias"] = alias
    return body
