# Day 23 — MCP server with per-user bearer tokens (read-only)

## Goal

A Python MCP server (`mcp` SDK, HTTP+SSE transport) at `https://tameru.app/mcp`. Exposes 4 read-only tools. Each request authenticates via a per-user bearer token from `mcp_tokens`. Settings UI generates and revokes tokens.

## Read first

- `DESIGN.md` §7.9 (MCP server — full auth flow), §8.6 (`mcp_tokens` schema).
- `CLAUDE.md` invariant 3.

## Deliverables

- Backend:
  - `app/mcp_server.py`:
    - Uses the `mcp` Python SDK.
    - HTTP+SSE transport, mounted at `/mcp` in the FastAPI app (or a separate ASGI app behind the same Railway service).
    - On each request, reads `Authorization: Bearer <token>`, computes `sha256(token)`, looks up `mcp_tokens` (must have `revoked_at IS NULL`), updates `last_used_at`, scopes all queries to the matched `user_id`.
    - Returns 401 if the token is missing, unknown, or revoked.
    - Exposes 4 tools, all read-only:
      - `get_spending_summary(date_from, date_to)` → category totals.
      - `get_recent_transactions(limit, category?)` → recent rows.
      - `get_subscriptions()` → active subscriptions.
      - `get_card_multipliers(card_name?)` → multipliers for one or all cards.
    - Each tool function uses `supabase_for_user(jwt)` where `jwt` is a short-lived JWT minted from the looked-up `user_id` (use Supabase's signing key) so RLS still fires. Or use the service role with manual `WHERE user_id = ?` — for read-only MCP, manual filter is acceptable since the surface is small. (Pick one and stick with it. JWT minting is purer; manual filter is simpler. Document the choice in code.)
  - `app/routes/mcp_tokens.py`:
    - `POST /mcp/tokens` → body `{name}`. Generates 32-byte random token, stores `sha256` hash, returns plaintext token **once**.
    - `GET /mcp/tokens` → list user's tokens with name, created_at, last_used_at, revoked status. Plaintext is never returned again.
    - `DELETE /mcp/tokens/{id}` → sets `revoked_at = now()`.
- Frontend:
  - `frontend/src/pages/Settings.tsx` — add "Connect to Claude.ai" section:
    - List existing tokens (name, last used, revoke button).
    - "Generate token" button → asks for a name → shows the plaintext once with a copy-to-clipboard button + a clear "this is the only time you'll see this token" warning.
    - Below: instructions for adding to Claude.ai's MCP config (`https://tameru.app/mcp` + `Authorization: Bearer <token>` header).
- Tests:
  - `tests/test_mcp.py`:
    - Generate a token, call each MCP tool, assert correct user-scoped data.
    - Revoke the token, call again, assert 401.
    - Use a token belonging to user A while querying — assert user B's data is not returned.

## Don't

- Don't expose any mutating tool over MCP. v1 is read-only. (See `CLAUDE.md` invariant 3.)
- Don't store tokens in plaintext at rest. Hash only.
- Don't return the plaintext token a second time. Once on creation, then never.

## Done when

- Adding the MCP URL + token to Claude.ai works: "Using my Tameru, how much did I spend on dining last month?" returns the right number.
- Revoking the token immediately blocks Claude.ai from getting answers.
- User A's token cannot read user B's data (RLS-equivalent enforcement).
