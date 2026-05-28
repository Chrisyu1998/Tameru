"""Read-only MCP server — OAuth 2.1 Resource Server (DESIGN.md §7.9).

Day 23a. Exposes Tameru spending data to MCP clients (Claude.ai, Claude
Code, Claude Desktop) over the Streamable HTTP transport. The server is
an OAuth 2.1 *Resource Server*: it validates the bearer access token and
serves data; it never issues or stores credentials. The Authorization
Server is Supabase Auth's OAuth 2.1 Server (CLAUDE.md invariant 3).

Auth — the RLS question is settled. A Supabase-OAuth access token is a
standard Supabase user JWT: `aud` and `role` are `authenticated`, `sub`
is the user id, with one extra `client_id` claim. It verifies through
the same JWKS / ES256 path as a browser session JWT
(`app.auth.verify_supabase_jwt`), and `supabase_for_user(token)` makes
Postgres enforce RLS on `auth.uid()` exactly as for a PWA request. So
the MCP server needs no service role and no manual `WHERE user_id`
filter — CLAUDE.md invariant 1 is untouched. A regular browser session
JWT also authenticates here: same user, same data, not a privilege gain.

Read-only by design (CLAUDE.md invariant 3). Every tool delegates to a
read function in `app.agent.tools`, so the MCP surface and the chat
agent's tool surface cannot drift. No propose / confirm / mutate tool is
exposed.

The server is mounted at `/mcp` by `app.main`; its session manager is
started in that module's `lifespan`.
"""

from __future__ import annotations

import os
from typing import Any

from urllib.parse import urlparse

import jwt
from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from app.agent.tools import get_cards as _agent_get_cards
from app.agent.tools import get_spending_summary as _agent_get_spending_summary
from app.agent.tools import get_subscriptions as _agent_get_subscriptions
from app.agent.tools import get_transactions as _agent_get_transactions
from app.auth import AuthedUser, verify_supabase_jwt

# get_recent_transactions limit bounds. The underlying agent tool caps at
# MAX_LIMIT regardless; clamping here turns a silly client input into a
# sane value instead of a 422 surfaced as an opaque MCP tool error.
_DEFAULT_RECENT_LIMIT = 20
_MAX_RECENT_LIMIT = 100

_MCP_INSTRUCTIONS = (
    "Tameru is a personal spending tracker. These tools are read-only: "
    "they answer questions about the signed-in user's transactions, "
    "spending by category, recurring subscriptions, and credit-card "
    "reward multipliers. They cannot create, edit, or delete anything."
)


class TameruTokenVerifier(TokenVerifier):
    """OAuth 2.1 Resource Server token check for the MCP server.

    Validates the bearer access token as a Supabase-signed JWT — the same
    JWKS / ES256 verification the rest of the backend uses (see
    `app.auth.verify_supabase_jwt`). Returns an `AccessToken` so the MCP
    SDK admits the request; returns `None` on any failure so the SDK
    answers `401` with a `WWW-Authenticate` header.

    No scopes are required in v1: a valid token grants the whole
    read-only surface, and Postgres RLS scopes the data to the token's
    user. Per-OAuth-client scoping (via the token's `client_id` claim) is
    a post-launch option, not a v1 concern.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an `AccessToken` for a valid Supabase JWT, else `None`."""
        try:
            verify_supabase_jwt(token)
        except HTTPException:
            # verify_supabase_jwt raises HTTPException(401) for a missing,
            # malformed, expired, or wrong-issuer token. Fail closed.
            return None
        claims = _unverified_claims(token)
        return AccessToken(
            token=token,
            # `client_id` identifies the OAuth client (e.g. Claude.ai) on
            # an OAuth-issued token; a plain browser session JWT has none,
            # so fall back to a constant. Metadata only — not a trust input.
            client_id=str(claims.get("client_id") or "tameru-session"),
            scopes=[],
            expires_at=claims.get("exp"),
        )


def mcp_auth_settings() -> AuthSettings:
    """Build the OAuth Resource Server settings for the MCP server.

    `issuer_url` is the Supabase Auth authorization server
    (`{SUPABASE_URL}/auth/v1`); `resource_server_url` is this MCP
    server's public URL, used in the RFC 9728 protected-resource metadata
    MCP clients discover. Both fall back to localhost defaults so the
    module imports cleanly when env vars are unset (tests, import-graph
    checks) — `app.main`'s boot-time check (`_REQUIRED_ENV_VARS`) is what
    enforces real values in a serving process.
    """
    supabase_url = (
        os.environ.get("SUPABASE_URL") or "http://localhost:54321"
    ).rstrip("/")
    resource_url = (
        os.environ.get("MCP_RESOURCE_SERVER_URL") or "http://localhost:8000/mcp"
    )
    return AuthSettings(
        issuer_url=AnyHttpUrl(f"{supabase_url}/auth/v1"),
        resource_server_url=AnyHttpUrl(resource_url),
        required_scopes=[],
    )


def mcp_transport_security() -> TransportSecuritySettings:
    """Build the DNS-rebinding allowlist from `MCP_RESOURCE_SERVER_URL`.

    The MCP SDK auto-enables DNS-rebinding protection with a
    localhost-only allowlist whenever FastMCP's default ``host="127.0.0.1"``
    is left in place (see mcp/server/fastmcp/server.py). Without an
    explicit override, every production request arrives with
    ``Host: tameru-production.up.railway.app`` and is rejected at the
    transport layer with HTTP 421 ("Invalid Host header") BEFORE auth
    runs — Claude.ai sees a generic "Authorization with the MCP server
    failed."

    Passing a `transport_security` value here bypasses the auto-enable
    block. We keep the protection on (cheap defense in depth, even
    behind OAuth) and derive the production host from the
    `MCP_RESOURCE_SERVER_URL` env var so prod and dev share one config
    surface. Local dev still works via the localhost entries.
    """
    resource_url = (
        os.environ.get("MCP_RESOURCE_SERVER_URL") or "http://localhost:8000/mcp"
    )
    netloc = urlparse(resource_url).netloc or "localhost:8000"
    allowed_hosts = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        netloc,
    ]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        f"https://{netloc}",
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


mcp_server = FastMCP(
    "Tameru",
    instructions=_MCP_INSTRUCTIONS,
    token_verifier=TameruTokenVerifier(),
    auth=mcp_auth_settings(),
    # Streamable HTTP, stateless + JSON responses — the SDK-recommended
    # production shape. `streamable_http_path="/"` so that when app.main
    # mounts this app at `/mcp` the endpoint resolves at exactly `/mcp`.
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=mcp_transport_security(),
)


@mcp_server.tool()
async def get_spending_summary(
    date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any]:
    """Per-category spending totals over a date window.

    Args:
        date_from: ISO date (YYYY-MM-DD) window start. Omit for the start
            of the current month.
        date_to: ISO date (YYYY-MM-DD) window end. Omit for today.

    Returns ``{window_start, window_end, window_months, breakdown:
    [{category, total, count}], truncated}``.
    """
    return _tool_get_spending_summary(_current_user(), date_from, date_to)


@mcp_server.tool()
async def get_recent_transactions(
    limit: int = _DEFAULT_RECENT_LIMIT, category: str | None = None
) -> dict[str, Any]:
    """Most recent transactions, newest first.

    Args:
        limit: how many rows to return (1-100; clamped into range).
        category: optional category filter. Must be one of Tameru's
            categories (Groceries, Dining, Coffee Shops, ...).

    Returns ``{items: [...], has_more: bool}``.
    """
    return _tool_get_recent_transactions(_current_user(), limit, category)


@mcp_server.tool()
async def get_subscriptions() -> dict[str, Any]:
    """The user's active recurring subscriptions, ordered by next billing date.

    Returns ``{items: [...], truncated: bool}``.
    """
    return _tool_get_subscriptions(_current_user())


@mcp_server.tool()
async def get_card_multipliers(card_name: str | None = None) -> dict[str, Any]:
    """Credit-card reward multipliers for the user's cards.

    Args:
        card_name: optional case-insensitive substring matching one card
            by name. Omit to return every card.

    Returns ``{items: [{name, issuer, network, program, last_four,
    multipliers}]}``.
    """
    return _tool_get_card_multipliers(_current_user(), card_name)


# The mountable Streamable HTTP ASGI app. `app.main` mounts this at /mcp
# and runs `mcp_server.session_manager` for the process lifetime.
mcp_app = mcp_server.streamable_http_app()

# The SDK builds the OAuth protected-resource metadata route at the
# absolute path it advertises in `WWW-Authenticate` —
# `/.well-known/oauth-protected-resource/mcp` (RFC 9728: the metadata
# lives at the host root, not under the resource path). Mounting `mcp_app`
# under `/mcp` would bury that route at `/mcp/.well-known/...`, where
# OAuth discovery clients never look. `app.main` re-registers these routes
# at the app root so the advertised URL resolves. The routes are
# self-contained (CORS-wrapped, no auth, no session-manager dependency),
# so re-registering them verbatim is safe.
mcp_well_known_routes = [
    route
    for route in mcp_app.routes
    if str(getattr(route, "path", "")).startswith("/.well-known/")
]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _current_user() -> AuthedUser:
    """Resolve the verified Tameru user for the in-flight MCP tool call.

    The access token was already validated by the Resource Server
    middleware (`TameruTokenVerifier`); this re-derives the `AuthedUser`
    from it so the tool can open an RLS-scoped Supabase client. Re-verify
    rather than thread state through the SDK — an ES256 decode against the
    cached JWKS is cheap, and each layer stays independently correct.
    """
    access = get_access_token()
    if access is None:
        # The SDK's auth middleware rejects an unauthenticated request
        # before any tool runs, so this is defensive only.
        raise PermissionError("MCP tool reached without a validated access token")
    return verify_supabase_jwt(access.token)


def _tool_get_spending_summary(
    user: AuthedUser, date_from: str | None, date_to: str | None
) -> dict[str, Any]:
    """Delegate to the chat agent's `get_spending_summary` read tool."""
    return _agent_get_spending_summary(user, date_from=date_from, date_to=date_to)


def _tool_get_recent_transactions(
    user: AuthedUser, limit: int, category: str | None
) -> dict[str, Any]:
    """Delegate to the chat agent's `get_transactions` read tool.

    `limit` is clamped to [1, _MAX_RECENT_LIMIT]; the agent tool orders by
    date descending, so a bare limit already yields "most recent".
    """
    clamped = max(1, min(int(limit), _MAX_RECENT_LIMIT))
    kwargs: dict[str, Any] = {"limit": clamped}
    if category is not None:
        kwargs["category"] = category
    return _agent_get_transactions(user, **kwargs)


def _tool_get_subscriptions(user: AuthedUser) -> dict[str, Any]:
    """Delegate to the chat agent's `get_subscriptions` read tool.

    Pinned to `status="active"` — an MCP client asking "what am I
    subscribed to" wants live subscriptions, not paused / cancelled
    history.
    """
    return _agent_get_subscriptions(user, status="active")


def _tool_get_card_multipliers(
    user: AuthedUser, card_name: str | None
) -> dict[str, Any]:
    """Delegate to `get_cards` and trim each row to the multiplier view.

    An optional `card_name` substring narrows to one card; the match is
    case-insensitive. A non-matching name yields an empty list (fail
    closed) rather than every card.
    """
    items = _agent_get_cards(user).get("items", [])
    if card_name:
        needle = card_name.strip().lower()
        items = [c for c in items if needle in str(c.get("name", "")).lower()]
    return {"items": [_card_multiplier_view(c) for c in items]}


def _card_multiplier_view(card: dict[str, Any]) -> dict[str, Any]:
    """Trim a full card row to the reward-multiplier fields MCP clients need."""
    return {
        "name": card.get("name"),
        "issuer": card.get("issuer"),
        "network": card.get("network"),
        "program": card.get("program"),
        "last_four": card.get("last_four"),
        "multipliers": card.get("multipliers"),
    }


def _unverified_claims(token: str) -> dict[str, Any]:
    """Decode a JWT's claims WITHOUT signature verification — metadata only.

    Called only after `verify_supabase_jwt` has already verified the
    signature, purely to read `client_id` / `exp` for the SDK's
    `AccessToken` record. Never use this to make a trust decision.
    """
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}
