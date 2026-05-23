# MCP server — Supabase OAuth 2.1 setup (Day 23a)

The read-only MCP server (`app/mcp_server.py`, DESIGN.md §7.9) is an
OAuth 2.1 **Resource Server**. The **Authorization Server** is Supabase
Auth's OAuth 2.1 Server — Tameru issues no MCP credentials of its own
(CLAUDE.md invariant 3).

This is dashboard configuration, not a schema change, so it lives here
as a checked-in note rather than in `supabase/migrations/`.

## One-time Supabase dashboard configuration

In the Supabase dashboard for the Tameru project:

1. Open **Authentication → OAuth Server** (a public-beta feature, shipped
   2025-11).
2. **Enable the OAuth 2.1 Server.**
3. **Enable Dynamic Client Registration.** MCP clients (Claude.ai,
   Claude Code, Claude Desktop) register themselves; without DCR each
   client would need a manually-created entry.
4. **Set the authorization (consent) path to `/oauth/consent`** — the
   route the PWA hosts. (Distinct from Supabase's own
   `{SUPABASE_URL}/oauth/authorize` endpoint — Tameru's `/oauth/consent`
   is the consent UI that endpoint delegates to.) Supabase's OAuth
   Server does not ship a hosted consent UI; Tameru renders consent
   itself using the user-facing `supabase.auth.oauth.*` methods
   (DESIGN.md §7.9, Day 23b).
5. Note the discovery URL Supabase exposes:
   `https://<project-ref>.supabase.co/.well-known/oauth-authorization-server/auth/v1`

Then, under **Project Settings → JWT Keys** (the *project-level* settings
page, not under Authentication — Authentication → Sessions controls
*refresh* tokens and Pro-gated session timeouts, which is a different
knob):

6. **Set `JWT expiry limit` = 300** (5 minutes). Default is 3600 (1
   hour). Direct URL:
   `https://supabase.com/dashboard/project/<project-ref>/settings/jwt`.

   Rationale: an OAuth grant revocation deletes the session + refresh
   token immediately, but the access JWT the MCP client already holds is
   stateless and stays valid until its `exp`. A short TTL is how the
   post-revocation residual window is bounded without adding a
   per-request session lookup at the MCP layer (which would force
   service-role access and amend CLAUDE.md invariant 1). 5 min ⇒ at most
   ~5 min between "user taps Disconnect" and "Claude.ai loses access."
   See DESIGN.md §7.9 (**Revocation**).

Tameru creates and stores no client secret — clients register
dynamically, and the user approves a consent screen at connect time.

## Environment variable

Set on the backend (Railway for production, `.env` for local runs):

| Variable | Value | Notes |
|---|---|---|
| `MCP_RESOURCE_SERVER_URL` | Public URL of the MCP endpoint, e.g. `https://tameru-production.up.railway.app/mcp` | Advertised in the RFC 9728 protected-resource metadata that MCP clients discover. **Required** — `app/main.py` refuses to boot without it. Local dev: `http://localhost:8000/mcp`. |

The OAuth `issuer_url` is derived from `SUPABASE_URL`
(`{SUPABASE_URL}/auth/v1`) — there is no separate variable for it.

## Why there is no `mcp_tokens` table

The earlier per-user bearer-token design needed a table; OAuth does not.
Supabase Auth stores the registered clients, grants, and tokens. The
`mcp_tokens` table is dropped in Day 23b.

## RLS — why the MCP server needs no service role

A Supabase-OAuth access token is a standard Supabase user JWT (`aud` /
`role` = `authenticated`, `sub` = user id, plus a `client_id` claim). It
verifies with the same JWKS / ES256 path as a browser session JWT, and
`supabase_for_user(token)` makes Postgres enforce RLS normally. The MCP
server therefore uses no service role and no manual `WHERE user_id`
filter — CLAUDE.md invariant 1 stands unchanged.

## What Day 23b covers

Connecting from Claude.ai web end-to-end, the Tameru-hosted consent
screen at `/oauth/consent`, the "Connected apps" Settings UI (calling
`supabase.auth.oauth.getUserGrants` / `revokeGrant` directly from the
PWA), the short-JWT-TTL configuration that bounds the post-revocation
window, and dropping the `mcp_tokens` table.
