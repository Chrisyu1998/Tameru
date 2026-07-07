"""MCP server tests (app/mcp_server.py) — Day 23a + 23b.

The MCP server is an OAuth 2.1 Resource Server. Its token check accepts
any valid Supabase JWT, and a Supabase OAuth-2.1-Server access token is
a standard user JWT (DESIGN.md §7.9) — the Resource Server cannot
distinguish the two and does not need to. So these tests exercise the
real auth + tool path with ordinary local-Supabase session JWTs; no
hosted-OAuth infrastructure is required.

Real local Supabase + real RLS, same posture as tests/test_tools.py: the
tools query Postgres under the user's JWT, so the cross-user isolation
assertions exercise the property that actually matters.

**Day 23b revoke-then-MCP-401 path — not automated.** The user-facing
end-to-end ("user taps Disconnect in Settings → Claude.ai loses access
within ~5 min") cannot be honestly automated in v1 for two structural
reasons, both documented in the day-23b prompt:

1. ``supabase-py`` 2.28 does not yet expose the ``auth.oauth.*`` methods
   (``listGrants`` / ``revokeGrant`` etc.). Those landed in
   ``@supabase/auth-js`` first; the Python SDK lags. So a Python test
   cannot perform the revoke step against local Supabase.
2. Even if it could, the access JWT Claude.ai already holds is
   *stateless* — Supabase's ``revokeGrant`` invalidates the session +
   refresh token immediately, but the access token signature remains
   valid until its ``exp``. Production bounds that residual window via
   the ``JWT expiry limit = 300s`` setting in
   ``supabase/MCP_OAUTH_SETUP.md``; a test would either need to wait 5
   real minutes or mint a JWT with a forged ``exp`` (we don't hold the
   ES256 signing key — see ``app/auth.py``).

What we do automate: the ``TameruTokenVerifier`` rejects malformed,
empty, and signature-corrupted tokens (this file), and the SDK's auth
middleware turns ``None`` from the verifier into ``401``. That covers
every code path the production revoke flow eventually hits. The
remaining end-to-end claim — "after Disconnect, Claude.ai gets 401" — is
a Day 28 UAT item.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.main import app
from app.mcp_server import (
    TameruTokenVerifier,
    _tool_get_card_credits,
    _tool_get_card_multipliers,
    _tool_get_recent_transactions,
    _tool_get_spending_summary,
    _tool_get_subscriptions,
    mcp_server,
)


# ===========================================================================
# Tool registration.
# ===========================================================================


def test_all_read_tools_are_registered():
    """The MCP surface is EXACTLY the read-only tools of DESIGN.md §7.9 / §6.7.

    Equality, not subset (audit P3-17): a subset match would stay green if
    a write tool were registered — and the write-invariant guard only walks
    the chat TOOL_REGISTRY, so an MCP-only mutation tool would pass both
    tests. CLAUDE.md invariant 3 makes adding an MCP write tool an
    explicit-approval event; this assertion is its mechanical hook.
    `get_card_credits` (credit tracking Phase 2, §6.7) is read-only.
    """
    names = {tool.name for tool in asyncio.run(mcp_server.list_tools())}
    assert names == {
        "get_spending_summary",
        "get_recent_transactions",
        "get_subscriptions",
        "get_card_multipliers",
        "get_card_credits",
    }


# ===========================================================================
# TameruTokenVerifier — the OAuth Resource Server token check.
# ===========================================================================


def test_verify_token_accepts_a_valid_supabase_jwt(user_a):
    """A real Supabase session JWT verifies and yields an AccessToken.

    A Supabase-OAuth access token is the same shape (DESIGN.md §7.9), so
    a session JWT is a faithful stand-in for the verifier's input.
    """
    access = asyncio.run(TameruTokenVerifier().verify_token(user_a.jwt))
    assert access is not None
    assert access.token == user_a.jwt


def test_verify_token_rejects_a_malformed_token(_supabase_stack_ready):
    """A non-JWT string fails verification — the SDK turns None into 401."""
    assert asyncio.run(TameruTokenVerifier().verify_token("not-a-jwt")) is None


def test_verify_token_rejects_an_empty_token(_supabase_stack_ready):
    """An empty bearer token is rejected, not treated as anonymous access."""
    assert asyncio.run(TameruTokenVerifier().verify_token("")) is None


def test_verify_token_rejects_a_corrupted_token(user_a):
    """A token with a corrupted signature fails — proves the JWKS check runs."""
    corrupted = user_a.jwt + "x"
    assert asyncio.run(TameruTokenVerifier().verify_token(corrupted)) is None


# ===========================================================================
# OAuth discovery — the mounted HTTP surface.
# ===========================================================================


def test_oauth_metadata_resolves_at_the_advertised_root_path():
    """The protected-resource metadata must resolve at the app root.

    RFC 9728 puts the metadata at /.well-known/oauth-protected-resource/mcp,
    and that root URL is what the WWW-Authenticate header advertises. The
    /mcp mount alone would bury it at /mcp/.well-known/... where OAuth MCP
    clients never look — app.main re-registers the route at the app root.
    """
    resp = TestClient(app).get("/.well-known/oauth-protected-resource/mcp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"].endswith("/mcp")
    assert body["authorization_servers"]


def test_mcp_transport_endpoint_rejects_an_unauthenticated_request():
    """The mounted /mcp transport is an OAuth Resource Server — no token, 401."""
    assert TestClient(app).post("/mcp").status_code == 401


# ===========================================================================
# get_spending_summary
# ===========================================================================


def test_get_spending_summary_returns_category_breakdown(user_a, card_a):
    """Transactions seeded in the current month appear in the breakdown."""
    tag = _tag()
    _seed_txn(user_a, card_id=card_a, merchant=f"Dine-{tag}", amount="12.00")
    _seed_txn(user_a, card_id=card_a, merchant=f"Dine2-{tag}", amount="8.00")
    result = _tool_get_spending_summary(_authed(user_a), None, None)
    assert set(result) == {
        "window_start",
        "window_end",
        "window_months",
        "breakdown",
        "truncated",
    }
    dining = [b for b in result["breakdown"] if b["category"] == "Dining"]
    assert dining, "seeded Dining spend should appear in the breakdown"
    # >= because user_a is session-scoped and accumulates rows across the suite.
    assert Decimal(dining[0]["total"]) >= Decimal("20.00")


# ===========================================================================
# get_recent_transactions
# ===========================================================================


def test_get_recent_transactions_returns_seeded_rows(user_a, card_a):
    """A just-seeded transaction is among the most-recent rows."""
    tag = _tag()
    _seed_txn(user_a, card_id=card_a, merchant=f"Recent-{tag}", amount="5.00")
    result = _tool_get_recent_transactions(_authed(user_a), 50, None)
    assert set(result) == {"items", "has_more"}
    assert f"Recent-{tag}" in {item["merchant"] for item in result["items"]}


def test_get_recent_transactions_category_filter(user_a, card_a):
    """The category filter passes through to the underlying query."""
    tag = _tag()
    _seed_txn(user_a, card_id=card_a, merchant=f"Din-{tag}", amount="9.00", category="Dining")
    _seed_txn(user_a, card_id=card_a, merchant=f"Gro-{tag}", amount="9.00", category="Groceries")
    merchants = {
        item["merchant"]
        for item in _tool_get_recent_transactions(_authed(user_a), 50, "Dining")["items"]
    }
    assert f"Din-{tag}" in merchants
    assert f"Gro-{tag}" not in merchants


def test_get_recent_transactions_isolates_users(user_a, user_b, card_a, card_b):
    """RLS: user A's MCP token cannot read user B's transactions."""
    tag = _tag()
    _seed_txn(user_a, card_id=card_a, merchant=f"A-only-{tag}", amount="3.00")
    _seed_txn(user_b, card_id=card_b, merchant=f"B-only-{tag}", amount="3.00")
    a_merchants = {
        i["merchant"]
        for i in _tool_get_recent_transactions(_authed(user_a), 50, None)["items"]
    }
    b_merchants = {
        i["merchant"]
        for i in _tool_get_recent_transactions(_authed(user_b), 50, None)["items"]
    }
    assert f"A-only-{tag}" in a_merchants and f"B-only-{tag}" not in a_merchants
    assert f"B-only-{tag}" in b_merchants and f"A-only-{tag}" not in b_merchants


# ===========================================================================
# get_subscriptions
# ===========================================================================


def test_get_subscriptions_returns_active_only(user_a, card_a):
    """The MCP tool pins status=active — paused / cancelled rows are excluded."""
    tag = _tag()
    _seed_sub(user_a, card_id=card_a, name=f"Active-{tag}", status="active")
    _seed_sub(user_a, card_id=card_a, name=f"Cancelled-{tag}", status="cancelled")
    result = _tool_get_subscriptions(_authed(user_a))
    assert set(result) == {"items", "truncated"}
    names = {s["name"] for s in result["items"]}
    assert f"Active-{tag}" in names
    assert f"Cancelled-{tag}" not in names


# ===========================================================================
# get_card_multipliers
# ===========================================================================


def test_get_card_multipliers_lists_cards(user_a, card_a):
    """Every returned card carries exactly the trimmed multiplier view."""
    items = _tool_get_card_multipliers(_authed(user_a), None)["items"]
    assert items, "user A has at least the card_a fixture"
    for item in items:
        assert set(item) == {
            "name",
            "issuer",
            "network",
            "program",
            "last_four",
            "multipliers",
        }


def test_get_card_multipliers_name_filter(user_a, card_a):
    """A name substring narrows to one card; a non-match yields nothing."""
    matched = _tool_get_card_multipliers(_authed(user_a), "A card")["items"]
    assert "A card" in {c["name"] for c in matched}
    assert _tool_get_card_multipliers(_authed(user_a), "no-such-card-zzz")["items"] == []


def test_get_card_multipliers_isolates_users(user_a, user_b, card_a, card_b):
    """RLS: user A's MCP token cannot read user B's cards."""
    a_names = {c["name"] for c in _tool_get_card_multipliers(_authed(user_a), None)["items"]}
    b_names = {c["name"] for c in _tool_get_card_multipliers(_authed(user_b), None)["items"]}
    assert "A card" in a_names and "B card" not in a_names
    assert "B card" in b_names and "A card" not in b_names


# ===========================================================================
# get_card_credits (Phase 2, §6.7)
# ===========================================================================


def test_mcp_get_card_credits_returns_credit_shape(user_a, card_a):
    """The MCP tool returns the same shape as the agent tool it delegates to."""
    name = f"Credit-{_tag()}"
    _seed_credit(user_a, card_a, name=name)
    result = _tool_get_card_credits(_authed(user_a), None)
    assert set(result) == {"credits", "truncated"}
    row = next(c for c in result["credits"] if c["name"] == name)
    assert set(row) >= {
        "name",
        "card_ref",
        "cadence",
        "amount",
        "used_amount",
        "remaining",
        "next_reset_date",
    }


def test_mcp_get_card_credits_name_filter_fails_closed(user_a, card_a):
    """A card_name matching no card yields no credits (fail closed), not all."""
    _seed_credit(user_a, card_a, name=f"Credit-{_tag()}")
    assert _tool_get_card_credits(_authed(user_a), "no-such-card-zzz") == {
        "credits": [],
        "truncated": False,
    }


def test_mcp_get_card_credits_isolates_users(user_a, user_b, card_a):
    """RLS: user B's MCP token cannot read user A's credits."""
    name = f"Private-{_tag()}"
    _seed_credit(user_a, card_a, name=name)
    b = _tool_get_card_credits(_authed(user_b), None)
    assert all(c["name"] != name for c in b["credits"])


def _seed_credit(user, card_id, *, name):
    """Insert an active card_credit directly under RLS (explicit period dates)."""
    supabase_for_user(user.jwt).table("card_credits").insert(
        {
            "user_id": user.id,
            "card_id": card_id,
            "name": name,
            "amount": "75",
            "cadence": "quarterly",
            "used_amount": "10",
            "current_period_start": "2026-07-01",
            "next_reset_date": "2026-10-01",
            "status": "active",
        }
    ).execute()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _tag() -> str:
    """Unique short tag so session-scoped fixture data doesn't collide."""
    return uuid.uuid4().hex[:8]


def _authed(user) -> AuthedUser:
    """Build the AuthedUser the tool functions expect from a TestUser fixture."""
    return AuthedUser(jwt=user.jwt, user_id=uuid.UUID(user.id), email=user.email)


def _seed_txn(user, *, card_id, merchant, amount, category="Dining"):
    """Insert one transaction via the user's RLS-scoped client; return its id."""
    client = supabase_for_user(user.jwt)
    row = {
        "user_id": user.id,
        "card_id": card_id,
        "merchant": merchant,
        "amount": amount,
        "date": date.today().isoformat(),
        "category": category,
        "source": "manual",
        "client_request_id": str(uuid.uuid4()),
    }
    return client.table("transactions").insert(row).execute().data[0]["id"]


def _seed_sub(user, *, card_id, name, status="active"):
    """Insert one subscription via the user's RLS-scoped client; return its id."""
    client = supabase_for_user(user.jwt)
    billing = (date.today() + timedelta(days=7)).isoformat()
    return (
        client.table("subscriptions")
        .insert(
            {
                "user_id": user.id,
                "card_id": card_id,
                "name": name,
                "amount": "9.99",
                "frequency": "monthly",
                "start_date": billing,
                "next_billing_date": billing,
                "category": "Streaming",
                "status": status,
            }
        )
        .execute()
        .data[0]["id"]
    )
