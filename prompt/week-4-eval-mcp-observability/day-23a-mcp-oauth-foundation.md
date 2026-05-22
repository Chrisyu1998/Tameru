# Day 23a — MCP server: OAuth 2.1 foundation + read-only tools

## Goal

A Python MCP server (`mcp` SDK, Streamable HTTP transport) on the Railway backend, exposing 4 read-only tools, authenticated as an OAuth 2.1 **Resource Server**. By end of day: a tool call carrying a valid OAuth access token returns correctly user-scoped data, an unauthenticated call returns `401`, and one user's token cannot read another user's data. Claude.ai-client wiring and the Settings UI are Day 23b.

## Read first

- `DESIGN.md` §7.9 (MCP server — OAuth 2.1 auth flow, **the RLS open item**), §7.2 (the chat agent's typed tools — reuse those query shapes).
- `CLAUDE.md` invariant 1 (RLS via JWT; service-role restrictions), invariant 3 (MCP read-only).
- `app/auth.py` — the existing Supabase-JWT verification (JWKS, ES256-pinned). MCP token validation reuses this; do not re-implement it.
- `app/db.py` — `supabase_for_user`.
- Supabase docs — "OAuth 2.1 Server" and "MCP Authentication" (the Authorization-Server side; this is a dashboard-config task, not a build).

## Deliverables

- **Supabase configuration:**
  - Enable the **OAuth 2.1 Server** and **Dynamic Client Registration** in the Supabase dashboard (Authentication → OAuth Server). Dashboard config is not a migration — record the exact steps in `supabase/` (a README note) so it is reproducible across environments.
- **`app/mcp_server.py`:**
  - `mcp` Python SDK, **Streamable HTTP** transport (`stateless_http`), mounted on the FastAPI app (or a sibling ASGI app behind the same Railway service).
  - OAuth Resource Server behavior: an unauthenticated request returns `401` with a `WWW-Authenticate` header; serve OAuth Protected Resource Metadata at `/.well-known/oauth-protected-resource` pointing at the Supabase authorization server.
  - On each request, validate the `Authorization: Bearer <token>` access token — a Supabase-signed JWT verified against the project JWKS (reuse `app/auth.py`'s verifier). Reject missing / malformed / invalid / expired tokens with `401`.
  - Expose 4 tools, all **read-only**:
    - `get_spending_summary(date_from, date_to)` → category totals.
    - `get_recent_transactions(limit, category?)` → recent rows.
    - `get_subscriptions()` → active subscriptions.
    - `get_card_multipliers(card_name?)` → multipliers for one or all cards.
- **RLS verification spike (do this before finalizing the auth path — see `DESIGN.md` §7.9 "RLS enforcement — open item"):**
  - Confirm whether the Supabase-OAuth-issued access token is directly usable as an RLS JWT via `supabase_for_user(token)` (depends on its `role` / `aud` claims being accepted by PostgREST).
  - **If yes:** each tool queries through `supabase_for_user(token)`; RLS enforces `auth.uid() = user_id`; `CLAUDE.md` invariant 1 is untouched. Record the verified claim shape in a code comment.
  - **If no:** resolve `user_id` from the validated token and query with the service role under an explicit `WHERE user_id = ?` on every tool. This **amends `CLAUDE.md` invariant 1** — add the MCP server as a third sanctioned service-role caller (alongside `pg_cron` and migrations), update invariant 1 + `DESIGN.md` §7.9, and add `app/mcp_server.py` to the allowlist in `tests/contracts/test_no_service_role_leak.py`. Get explicit user sign-off on the invariant change before taking this path.
- **`pyproject.toml`:** add the `mcp` SDK to `dependencies`.
- **Tests — `tests/test_mcp.py`:**
  - Call each tool with a valid token; assert correct user-scoped data.
  - Seed data for user A and user B; call with user A's token; assert user B's data never appears.
  - Missing / malformed / expired token → `401`.

## Don't

- Don't expose any mutating tool over MCP. v1 is read-only (`CLAUDE.md` invariant 3).
- Don't build a bespoke OAuth authorization server or bring in a third-party auth vendor — Supabase's OAuth 2.1 Server is the Authorization Server.
- Don't use the "HTTP+SSE" transport — it is the deprecated MCP transport. Streamable HTTP only.
- Don't take the service-role path without first running the RLS spike and getting sign-off on the invariant-1 amendment.

## Done when

- A tool call carrying a valid Supabase-OAuth access token returns correctly user-scoped data.
- A request with a missing / invalid / expired token returns `401`.
- User A's token cannot read user B's data.
- The RLS open item is resolved — either RLS-via-JWT is verified (invariant 1 intact) or the service-role path landed with the invariant-1 amendment and the leak-guard allowlist updated.
