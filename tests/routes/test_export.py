"""GET /export — user data export, Day 27 (DESIGN.md §9.6).

Exercises the full chain: HTTP handler → `supabase_for_user(user.jwt)`
→ per-table reads under RLS → single JSON object download. Tests seed
real rows into the local Supabase stack via each user's own JWT so the
RLS-scoped `SELECT *` fires exactly as it will in production.

The two load-bearing properties under test:

  1. The export returns the user's own rows across every v1-scoped
     table — and ONLY those rows. A cross-tenant assertion catches the
     regression where a future change drops the RLS-aware client and
     reaches for service-role.
  2. The v1 inclusion list is exactly seven keys plus three metadata
     fields. The exclusion list (`chat_turn_trace`, `ai_call_log`,
     `email_log`) is greppable in the route module docstring and is
     asserted here as well — promoting any of them is a deliberate
     scope expansion that should fail this test first.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provide a fresh TestClient for the FastAPI app under test."""
    return TestClient(app)


def test_export_returns_seven_table_keys_plus_metadata(client, user_a, card_a):
    """The dump carries the v1 inclusion list and the three metadata fields."""
    resp = client.get("/export", headers=_auth(user_a))
    assert resp.status_code == 200, resp.text

    payload = json.loads(resp.content)
    assert set(payload.keys()) == {
        # Metadata
        "user_id",
        "exported_at",
        "schema_version",
        # User content
        "transactions",
        "cards",
        "subscriptions",
        "user_memory",
        "chat_messages",
        "merchant_category",
        "users_meta",
    }
    assert payload["user_id"] == user_a.id
    assert payload["schema_version"] == 1


def test_export_excludes_observability_tables(client, user_a):
    """`chat_turn_trace`, `ai_call_log`, `email_log` are NOT exported in v1.

    These are internal observability records, not user data. Promoting
    any of them is a deliberate scope expansion (see route docstring) —
    this test fails first when someone tries.
    """
    resp = client.get("/export", headers=_auth(user_a))
    payload = json.loads(resp.content)

    for excluded in ("chat_turn_trace", "ai_call_log", "ai_call_log_daily", "email_log"):
        assert excluded not in payload, (
            f"{excluded!r} appeared in the export — observability "
            "tables are excluded from v1 by design. If this is a "
            "deliberate scope expansion, update app/routes/export.py's "
            "module docstring and this test together."
        )


def test_export_returns_callers_own_rows(client, user_a, card_a):
    """Seeded transactions land in the export under their own keys.

    Sanity check that the response isn't an empty-array stub — at least
    the seeded row's merchant should show up. Doesn't deeply assert
    column shapes (those are pinned by individual table tests); the
    invariant under test is "data appears."
    """
    tag = uuid.uuid4().hex[:8]
    merchant = f"ExportSeed-{tag}"
    _seed_transaction(user_a, card_a, merchant=merchant)

    try:
        resp = client.get("/export", headers=_auth(user_a))
        assert resp.status_code == 200
        payload = json.loads(resp.content)

        merchants = [row["merchant"] for row in payload["transactions"]]
        assert merchant in merchants

        # Card fixture must show up too; at least one card row exists.
        assert len(payload["cards"]) >= 1
    finally:
        _delete_seeded_transaction(user_a, merchant)


def test_export_does_not_leak_cross_tenant_rows(
    client, user_a, user_b, card_a, card_b
):
    """user_a's export excludes any row owned by user_b.

    The RLS regression guard: if a future refactor drops
    `supabase_for_user` in favor of service role, this test catches it
    before users see each other's data.
    """
    tag = uuid.uuid4().hex[:8]
    a_merchant = f"AOnly-{tag}"
    b_merchant = f"BOnly-{tag}"
    _seed_transaction(user_a, card_a, merchant=a_merchant)
    _seed_transaction(user_b, card_b, merchant=b_merchant)

    try:
        resp = client.get("/export", headers=_auth(user_a))
        assert resp.status_code == 200
        payload = json.loads(resp.content)

        merchants = {row["merchant"] for row in payload["transactions"]}
        assert a_merchant in merchants
        assert b_merchant not in merchants, (
            "RLS bypass: user_a's export contained a user_b row. "
            "Check that the export route uses supabase_for_user(jwt), "
            "not supabase_admin()."
        )

        # And user_a's row count for cards excludes b_card.
        card_ids = {row["id"] for row in payload["cards"]}
        assert card_a in card_ids
        assert card_b not in card_ids
    finally:
        _delete_seeded_transaction(user_a, a_merchant)
        _delete_seeded_transaction(user_b, b_merchant)


def test_export_sets_attachment_disposition(client, user_a):
    """Response carries `Content-Disposition: attachment` with today's date.

    Triggers a browser download instead of an in-tab render. Filename
    embeds the export date so multiple same-day downloads de-dup via OS
    naming, not silent overwrite.
    """
    resp = client.get("/export", headers=_auth(user_a))
    assert resp.status_code == 200

    cd = resp.headers.get("content-disposition", "")
    assert cd.startswith("attachment;")
    assert "tameru-export-" in cd
    assert date.today().isoformat() in cd
    assert resp.headers.get("cache-control") == "no-store"


def test_export_requires_auth(client):
    """No bearer token → 401, not an empty export."""
    resp = client.get("/export")
    assert resp.status_code == 401


def test_export_requires_device_id(client, user_a):
    """Bearer JWT without X-Device-Id → device gate refuses (401)."""
    resp = client.get(
        "/export",
        headers={"Authorization": f"Bearer {user_a.jwt}"},
    )
    assert resp.status_code == 401


def test_export_users_meta_is_single_object_or_null(client, user_a):
    """`users_meta` is the only 1:1-per-user table and exports as object/null."""
    resp = client.get("/export", headers=_auth(user_a))
    payload = json.loads(resp.content)
    meta = payload["users_meta"]
    # user_a is bootstrapped, so the row exists.
    assert isinstance(meta, dict)
    assert meta["user_id"] == user_a.id


def test_export_paginates_past_postgrest_page_cap(
    client, user_a, card_a, monkeypatch
):
    """Rows beyond a single PostgREST page still land in the export.

    PostgREST caps every `SELECT` at `max-rows` (1000 on Supabase by
    default). `_select_all` pages through with `.range()` until a page
    returns short. Without that loop a heavy user would download a
    truncated file and never know.

    Driving the test at the real 1000-row cap would be slow; instead
    we monkeypatch the page size down to 2, seed enough rows to require
    three pages, and assert the response contains every seeded row.
    The loop logic is identical at any page size.
    """
    from app.routes import export as export_route

    monkeypatch.setattr(export_route, "_EXPORT_PAGE_SIZE", 2)

    tag = uuid.uuid4().hex[:8]
    merchants = [f"Page{i}-{tag}" for i in range(5)]
    for merchant in merchants:
        _seed_transaction(user_a, card_a, merchant=merchant)

    try:
        resp = client.get("/export", headers=_auth(user_a))
        assert resp.status_code == 200, resp.text
        payload = json.loads(resp.content)

        exported_merchants = {row["merchant"] for row in payload["transactions"]}
        for merchant in merchants:
            assert merchant in exported_merchants, (
                f"row {merchant!r} missing from export — pagination "
                "loop returned a truncated result. _select_all must "
                "iterate .range() until a page returns short."
            )
    finally:
        for merchant in merchants:
            _delete_seeded_transaction(user_a, merchant)


def test_hardening_headers_present_on_export(client, user_a):
    """Day 27 JSON-API hardening headers ride out on the export response.

    The middleware is global, so /export inherits it. This test pins
    the property at the route most likely to be downloaded and forwarded
    — if a future change strips the hardening for /export specifically
    (e.g., to embed the JSON in a page), the regression surfaces here.
    """
    resp = client.get("/export", headers=_auth(user_a))
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Build the Bearer + X-Device-Id headers the device gate requires."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _seed_transaction(user, card_id: str, *, merchant: str) -> None:
    """Insert one transaction row via the user's RLS-scoped client.

    Test-only convenience: bypasses the propose-confirm flow (which
    runs the agent loop) because this suite asserts the *export* shape,
    not the entry path.
    """
    client = supabase_for_user(user.jwt)
    client.table("transactions").insert(
        {
            "user_id": user.id,
            "card_id": card_id,
            "merchant": merchant,
            "amount": str(Decimal("9.99")),
            "date": date.today().isoformat(),
            "category": "Coffee Shops",
            "source": "manual",
        }
    ).execute()


def _delete_seeded_transaction(user, merchant: str) -> None:
    """Clean up the test's seeded row so it doesn't pollute downstream tests."""
    client = supabase_for_user(user.jwt)
    client.table("transactions").delete().eq("user_id", user.id).eq(
        "merchant", merchant
    ).execute()
