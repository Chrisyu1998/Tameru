"""Phase 1 — card statement-credit API contract suite (DESIGN.md §6.7, §8.17).

Covers the TODO.md Phase-1 test checklist:

- `POST /card-credits/lookup`: returns proposals with minted crids; fail-closed
  (needs_manual + empty list) on a lookup miss; 404 on another user's card.
- `POST /card-credits/confirm`: happy path seeds period bounds via
  `credit_period_bounds()` + used_amount=0; idempotency (crid + name partial
  index) — a replay lands no new row; a forged card_id is dropped.
- `GET /card-credits`: active by default; `?include_archived=true`.
- `PATCH /card-credits/{id}`: set-used-amount; a cadence change recomputes the
  period bounds.
- `DELETE /card-credits/{id}`: archives (never hard-deletes); hidden from the
  default list.
- Card soft-delete archives companion credits (soft_delete_card cascade).
- RLS: user B cannot read / patch / delete / confirm onto user A's credits.

`lookup_card_credits` is monkeypatched so no real Anthropic call fires. Credit
names are tagged per test so the `(card_id, lower(name))` partial unique index
doesn't collide across tests sharing the session-scoped card_a.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app
from app.models.card_credits import CardCreditsLookupResult, LookedUpCredit
from app.routes import card_credits as card_credits_route


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient reusing the running stack from conftest."""
    return TestClient(app)


@pytest.fixture
def stub_credit_lookup(monkeypatch):
    """Replace `lookup_card_credits` with a synchronous, offline stub.

    Default: two credits (a quarterly Lululemon + an annual airline fee).
    Tests override via the returned setter.
    """
    state: dict[str, CardCreditsLookupResult] = {
        "result": CardCreditsLookupResult(
            credits=[
                LookedUpCredit(
                    name="Lululemon credit",
                    amount=75,
                    cadence="quarterly",
                    merchant_hint="lululemon",
                ),
                LookedUpCredit(
                    name="Airline fee credit",
                    amount=200,
                    cadence="annual",
                    merchant_hint=None,
                ),
            ],
            source_urls=["https://nerdwallet.com/amex-platinum"],
            needs_manual=False,
        )
    }

    def fake_lookup(card_name: str, user, home_currency: str = "USD"):  # noqa: ARG001
        """Return the canned result; ignore args."""
        return state["result"]

    monkeypatch.setattr(card_credits_route, "lookup_card_credits", fake_lookup)

    def set_result(result: CardCreditsLookupResult) -> None:
        """Override the canned result mid-test."""
        state["result"] = result

    return set_result


# ---------------------------------------------------------------------------
# POST /card-credits/lookup
# ---------------------------------------------------------------------------


def test_lookup_returns_proposals_with_minted_crids(
    client, user_a, card_a, stub_credit_lookup
):
    """Lookup returns one CreditProposal per discovered credit, each with a crid."""
    resp = client.post(
        "/card-credits/lookup", headers=_auth(user_a), json={"card_id": card_a}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["card_id"] == card_a
    assert body["card_name"] == "A card"
    assert len(body["credits"]) == 2
    for c in body["credits"]:
        assert c["card_id"] == card_a
        assert uuid.UUID(c["client_request_id"])  # server-minted, valid UUID
        assert c["verified_at"] is not None
        assert c["source_urls"]
    lulu = next(c for c in body["credits"] if c["name"] == "Lululemon credit")
    assert lulu["cadence"] == "quarterly"
    assert lulu["merchant_hint"] == "lululemon"


def test_lookup_fail_closed_returns_empty_needs_manual(
    client, user_a, card_a, stub_credit_lookup
):
    """A lookup miss returns HTTP 200 with an empty list + needs_manual=True."""
    stub_credit_lookup(
        CardCreditsLookupResult(credits=[], needs_manual=True, raw_text="nothing found")
    )
    resp = client.post(
        "/card-credits/lookup", headers=_auth(user_a), json={"card_id": card_a}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["credits"] == []
    assert body["needs_manual"] is True


def test_lookup_on_other_users_card_is_404(
    client, user_a, card_b, stub_credit_lookup
):
    """RLS: user A cannot look up credits on user B's card (card_b → 404)."""
    resp = client.post(
        "/card-credits/lookup", headers=_auth(user_a), json={"card_id": card_b}
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# POST /card-credits/confirm
# ---------------------------------------------------------------------------


def test_confirm_seeds_period_bounds_and_zero_used(client, user_a, card_a):
    """Confirm writes rows with used_amount=0 and period bounds from the helper."""
    name = f"Confirm credit {_tag()}"
    proposal = _proposal(card_a, name=name, amount="75", cadence="quarterly")
    resp = client.post(
        "/card-credits/confirm", headers=_auth(user_a), json={"credits": [proposal]}
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    row = items[0]
    assert row["used_amount"] in ("0", "0.0")  # numeric 0
    assert row["status"] == "active"
    assert row["amount"] == "75"

    # The seeded bounds match credit_period_bounds() for the user's local today.
    expected = _period_bounds(user_a, "quarterly")
    assert row["current_period_start"] == expected["period_start"]
    assert row["next_reset_date"] == expected["next_reset"]


def test_confirm_is_idempotent_on_replay(client, user_a, card_a):
    """Re-confirming the same credit (same name) lands no second active row."""
    name = f"Idem credit {_tag()}"
    proposal = _proposal(card_a, name=name, amount="50", cadence="monthly")
    first = client.post(
        "/card-credits/confirm", headers=_auth(user_a), json={"credits": [proposal]}
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 1

    # A new crid but the same (card_id, name) — the name partial index dedups.
    replay = dict(proposal, client_request_id=str(uuid.uuid4()))
    second = client.post(
        "/card-credits/confirm", headers=_auth(user_a), json={"credits": [replay]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["items"] == []  # deduped, nothing landed

    # Exactly one active row exists for that name.
    listed = client.get(
        "/card-credits", headers=_auth(user_a), params={"card_id": card_a}
    )
    matches = [r for r in listed.json()["items"] if r["name"] == name]
    assert len(matches) == 1


def test_confirm_drops_credit_on_foreign_card(client, user_a, card_b):
    """A credit whose card_id is not the caller's active card is silently dropped."""
    proposal = _proposal(card_b, name=f"Forged {_tag()}", amount="10", cadence="monthly")
    resp = client.post(
        "/card-credits/confirm", headers=_auth(user_a), json={"credits": [proposal]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []  # EXISTS filter dropped it


def test_confirm_amount_null_is_allowed(client, user_a, card_a):
    """A fail-closed proposal (amount omitted) confirms with null amount."""
    name = f"Null amount {_tag()}"
    proposal = _proposal(card_a, name=name, amount=None, cadence="annual")
    resp = client.post(
        "/card-credits/confirm", headers=_auth(user_a), json={"credits": [proposal]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["amount"] is None


# ---------------------------------------------------------------------------
# GET / PATCH / DELETE
# ---------------------------------------------------------------------------


def test_get_lists_active_and_optionally_archived(client, user_a, card_a):
    """GET returns active credits by default; include_archived surfaces archived."""
    name = f"Listed {_tag()}"
    credit_id = _create_credit(client, user_a, card_a, name=name, cadence="monthly")

    active = client.get(
        "/card-credits", headers=_auth(user_a), params={"card_id": card_a}
    )
    assert any(r["id"] == credit_id for r in active.json()["items"])

    client.delete(f"/card-credits/{credit_id}", headers=_auth(user_a))
    after = client.get(
        "/card-credits", headers=_auth(user_a), params={"card_id": card_a}
    )
    assert not any(r["id"] == credit_id for r in after.json()["items"])
    with_archived = client.get(
        "/card-credits",
        headers=_auth(user_a),
        params={"card_id": card_a, "include_archived": "true"},
    )
    assert any(r["id"] == credit_id for r in with_archived.json()["items"])


def test_patch_set_used_amount(client, user_a, card_a):
    """PATCH used_amount is the set-used-amount action."""
    credit_id = _create_credit(client, user_a, card_a, name=f"Used {_tag()}", cadence="quarterly")
    resp = client.patch(
        f"/card-credits/{credit_id}",
        headers=_auth(user_a),
        json={"used_amount": "60"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["used_amount"] == "60"


def test_patch_cadence_recomputes_period_bounds(client, user_a, card_a):
    """Changing cadence recomputes current_period_start / next_reset_date."""
    credit_id = _create_credit(
        client, user_a, card_a, name=f"Cadence {_tag()}", cadence="monthly"
    )
    resp = client.patch(
        f"/card-credits/{credit_id}",
        headers=_auth(user_a),
        json={"cadence": "annual"},
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["cadence"] == "annual"
    expected = _period_bounds(user_a, "annual")
    assert row["current_period_start"] == expected["period_start"]
    assert row["next_reset_date"] == expected["next_reset"]


def test_delete_archives_not_hard_deletes(client, user_a, card_a):
    """DELETE flips status='archived'; the row survives (re-add allowed)."""
    credit_id = _create_credit(client, user_a, card_a, name=f"Archive {_tag()}", cadence="monthly")
    resp = client.delete(f"/card-credits/{credit_id}", headers=_auth(user_a))
    assert resp.status_code == 204, resp.text
    admin = supabase_for_user(user_a.jwt)
    row = admin.table("card_credits").select("status").eq("id", credit_id).execute().data
    assert row and row[0]["status"] == "archived"


# ---------------------------------------------------------------------------
# Card soft-delete cascade
# ---------------------------------------------------------------------------


def test_card_soft_delete_archives_companion_credits(client, user_a):
    """Soft-deleting a card flips its active credits to 'archived' (§8.3 cascade)."""
    card_id = _make_card(user_a)
    credit_id = _create_credit(client, user_a, card_id, name="Cascade credit", cadence="monthly")

    resp = client.delete(f"/cards/{card_id}", headers=_auth(user_a))
    assert resp.status_code == 204, resp.text

    admin = supabase_for_user(user_a.jwt)
    row = admin.table("card_credits").select("status").eq("id", credit_id).execute().data
    assert row and row[0]["status"] == "archived"


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------


def test_rls_user_b_cannot_see_user_a_credit(client, user_a, user_b, card_a):
    """User B's GET of user A's card returns nothing (RLS + ownership)."""
    _create_credit(client, user_a, card_a, name=f"Private {_tag()}", cadence="monthly")
    # card_a is user A's card, so user B gets 404 resolving it.
    resp = client.get(
        "/card-credits", headers=_auth(user_b), params={"card_id": card_a}
    )
    assert resp.json()["items"] == []


def test_rls_user_b_cannot_patch_user_a_credit(client, user_a, user_b, card_a):
    """User B cannot PATCH user A's credit (RLS scopes the row invisible → 404)."""
    credit_id = _create_credit(client, user_a, card_a, name=f"NoPatch {_tag()}", cadence="monthly")
    resp = client.patch(
        f"/card-credits/{credit_id}",
        headers=_auth(user_b),
        json={"used_amount": "5"},
    )
    assert resp.status_code == 404, resp.text


def test_rls_user_b_delete_is_noop_on_user_a_credit(client, user_a, user_b, card_a):
    """User B's DELETE of user A's credit is a no-op (row stays active)."""
    credit_id = _create_credit(client, user_a, card_a, name=f"NoDelete {_tag()}", cadence="monthly")
    resp = client.delete(f"/card-credits/{credit_id}", headers=_auth(user_b))
    assert resp.status_code == 204  # indistinguishable, but no mutation
    admin = supabase_for_user(user_a.jwt)
    row = admin.table("card_credits").select("status").eq("id", credit_id).execute().data
    assert row and row[0]["status"] == "active"


def test_rls_rejects_direct_insert_onto_foreign_card(user_a, card_b):
    """RLS (not just the route) refuses a credit attached to another user's card.

    A direct PostgREST insert with the caller's own user_id but user B's
    card_id must be rejected by the INSERT WITH CHECK card-ownership predicate —
    otherwise the confirm route could be bypassed to create cross-tenant FK
    attachments / a card-existence oracle (Codex 2026-07-05 P2).
    """
    a_client = supabase_for_user(user_a.jwt)
    with pytest.raises(Exception):
        (
            a_client.table("card_credits")
            .insert(
                {
                    "user_id": user_a.id,
                    "card_id": card_b,  # user B's card
                    "name": f"RLS probe {_tag()}",
                    "cadence": "monthly",
                    "current_period_start": "2026-07-01",
                    "next_reset_date": "2026-08-01",
                }
            )
            .execute()
        )


def test_rls_allows_direct_insert_onto_owned_card(user_a, card_a):
    """Positive control: the ownership predicate doesn't over-block owned cards."""
    a_client = supabase_for_user(user_a.jwt)
    resp = (
        a_client.table("card_credits")
        .insert(
            {
                "user_id": user_a.id,
                "card_id": card_a,  # user A's own card
                "name": f"RLS ok {_tag()}",
                "cadence": "monthly",
                "current_period_start": "2026-07-01",
                "next_reset_date": "2026-08-01",
            }
        )
        .execute()
    )
    assert resp.data and resp.data[0]["card_id"] == card_a


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Build the bearer + device headers for the authenticated route gate."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _tag() -> str:
    """Short random suffix so per-test credit names don't collide on the name index."""
    return uuid.uuid4().hex[:8]


def _proposal(
    card_id: str, *, name: str, amount: str | None, cadence: str
) -> dict:
    """Build a CreditProposal-shaped dict for the confirm body."""
    p: dict[str, object] = {
        "card_id": card_id,
        "name": name,
        "cadence": cadence,
        "source_urls": [],
        "client_request_id": str(uuid.uuid4()),
    }
    if amount is not None:
        p["amount"] = amount
    return p


def _create_credit(client, user, card_id: str, *, name: str, cadence: str) -> str:
    """Confirm one credit and return its id (test setup helper)."""
    resp = client.post(
        "/card-credits/confirm",
        headers=_auth(user),
        json={"credits": [_proposal(card_id, name=name, amount="25", cadence=cadence)]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"][0]["id"]


def _make_card(user) -> str:
    """Insert a fresh active card (own row so soft-delete tests don't touch card_a)."""
    client = supabase_for_user(user.jwt)
    last_four = uuid.uuid4().hex[:4].translate(str.maketrans("abcdef", "012345"))
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user.id,
                "name": f"Credit test card {_tag()}",
                "issuer": "other",
                "program": "Other",
                "network": "visa",
                "last_four": last_four,
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


def _period_bounds(user, cadence: str) -> dict[str, str]:
    """Call credit_period_bounds() for the user's local today (expected-value helper)."""
    from app.util.timezone import user_local_today

    client = supabase_for_user(user.jwt)
    today = user_local_today(user.jwt)
    data = client.rpc(
        "credit_period_bounds",
        {"p_cadence": cadence, "p_on_date": today.isoformat()},
    ).execute().data
    return {
        "period_start": data[0]["period_start"],
        "next_reset": data[0]["next_reset"],
    }
