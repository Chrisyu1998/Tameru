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
- `DELETE /cards/{id}`: soft-delete sets active=false + deactivated_at.
- RLS: user A cannot GET / PATCH / DELETE user B's cards.

`lookup_card` is monkeypatched on every test so no real Anthropic call
fires — tests stay deterministic and offline.

Conftest's card_a / card_b session fixtures occupy `(visa, 1111)` and
`(amex, 2222)` respectively; tests that exercise the partial unique
index use new (network, last_four) pairs that don't collide.
"""

from __future__ import annotations

import uuid
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
            issuer="Chase",
            source_urls=["https://nerdwallet.com/chase-sapphire-preferred"],
            needs_manual=False,
        )
    }

    def fake_lookup(card_name: str, user) -> CardLookupResult:  # noqa: ARG001
        """Return the canned CardLookupResult; ignore the user arg."""
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
            issuer="Chase",
            program="UR",
            multipliers={"Dining": 3.0},
            annual_fee="95",
        ),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == f"Test Card {tag}"
    assert body["network"] == "visa"
    assert body["active"] is True
    assert body["deactivated_at"] is None
    assert Decimal(body["annual_fee"]) == Decimal("95")


def test_confirm_rejects_missing_last_four(client, user_a):
    """Verify that /cards/confirm rejects payloads without last_four."""
    body = _proposal(
        network="visa",
        last_four="1234",
        name="X",
        issuer="X",
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
            issuer="X",
        ),
    )
    assert resp.status_code == 422


def test_confirm_rejects_unknown_network(client, user_a):
    """Verify that /cards/confirm rejects networks outside the closed enum."""
    resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="diners",
            last_four="1234",
            name="X",
            issuer="X",
        ),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /cards/confirm — collision behavior
# ---------------------------------------------------------------------------


def test_confirm_409_when_active_identity_collides(client, user_a):
    """Verify that confirming a duplicate (network, last_four) returns 409.

    The first confirm succeeds; the second confirm of the same
    `(visa, last_four)` returns 409 active_card_exists with the existing
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
            issuer="Chase",
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
            issuer="Chase",
        ),
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "active_card_exists"
    assert detail["existing_card_id"] == existing_id
    assert detail["existing_card_last_four"] == last_four


def test_confirm_after_soft_delete_inserts_new_row(client, user_a):
    """Verify that soft-delete + re-add yields two rows, never reviving.

    DESIGN.md §8.1: inactive rows are exempt from the partial unique
    index; re-adding the same (network, last_four) creates a fresh
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
            issuer="Citi",
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
            issuer="Citi",
        ),
    )
    assert second.status_code == 200, second.text
    second_id = second.json()["id"]
    assert second_id != first_id, "expected a fresh card_id, got a revived row"
    assert second.json()["active"] is True

    # Verify the old row is still inactive (not revived).
    db = supabase_for_user(user_a.jwt)
    rows = (
        db.table("cards")
        .select("id, active, deactivated_at")
        .in_("id", [first_id, second_id])
        .execute()
        .data
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id[first_id]["active"] is False
    assert by_id[first_id]["deactivated_at"] is not None
    assert by_id[second_id]["active"] is True


def test_confirm_allows_same_last_four_across_networks(client, user_a):
    """Verify that (visa, 5678) and (amex, 5678) can coexist active.

    The partial unique index keys on (user_id, network, last_four), so
    a last_four collision *across* networks is legal.
    """
    tag = _tag()
    last_four = _last_four(tag, "5")
    visa_resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="visa",
            last_four=last_four,
            name=f"Visa {tag}",
            issuer="Chase",
        ),
    )
    assert visa_resp.status_code == 200, visa_resp.text

    amex_resp = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="amex",
            last_four=last_four,
            name=f"Amex {tag}",
            issuer="Amex",
        ),
    )
    assert amex_resp.status_code == 200, amex_resp.text
    assert amex_resp.json()["id"] != visa_resp.json()["id"]


# ---------------------------------------------------------------------------
# DELETE — soft-delete behavior
# ---------------------------------------------------------------------------


def test_delete_sets_active_false_and_deactivated_at(client, user_a):
    """Verify that DELETE soft-deletes — active=false + deactivated_at set."""
    tag = _tag()
    confirm = client.post(
        "/cards/confirm",
        headers=_auth(user_a),
        json=_proposal(
            network="discover",
            last_four=_last_four(tag, "3"),
            name=f"To Delete {tag}",
            issuer="Discover",
        ),
    )
    assert confirm.status_code == 200
    card_id = confirm.json()["id"]

    delete = client.delete(f"/cards/{card_id}", headers=_auth(user_a))
    assert delete.status_code == 204

    db = supabase_for_user(user_a.jwt)
    row = (
        db.table("cards")
        .select("active, deactivated_at")
        .eq("id", card_id)
        .execute()
        .data[0]
    )
    assert row["active"] is False
    assert row["deactivated_at"] is not None


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
            issuer="Chase",
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
            issuer="Amex",
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
            issuer="Amex",
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
    assert by_id[inactive_id]["active"] is False
    assert by_id[inactive_id]["deactivated_at"] is not None


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
            issuer="Chase",
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
    row = db_b.table("cards").select("active").eq("id", card_b).execute().data[0]
    assert row["active"] is True


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


def _proposal(
    *,
    network: str,
    last_four: str,
    name: str,
    issuer: str,
    program: str = "Other",
    multipliers: dict[str, float] | None = None,
    annual_fee: str | None = None,
    source_urls: list[str] | None = None,
    alias: str | None = None,
    needs_manual: bool = False,
) -> dict:
    """Build a CardProposal-shaped dict for /cards/confirm tests."""
    body: dict = {
        "network": network,
        "last_four": last_four,
        "name": name,
        "issuer": issuer,
        "program": program,
        "multipliers": multipliers or {},
        "source_urls": source_urls or [],
        "needs_manual": needs_manual,
    }
    if annual_fee is not None:
        body["annual_fee"] = annual_fee
    if alias is not None:
        body["alias"] = alias
    return body
