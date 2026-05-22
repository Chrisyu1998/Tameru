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
4. Note the discovery URL Supabase exposes:
   `https://<project-ref>.supabase.co/.well-known/oauth-authorization-server/auth/v1`

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

Connecting from Claude.ai web end-to-end, the consent screen, the
"Connected apps" Settings UI, and dropping the `mcp_tokens` table.
