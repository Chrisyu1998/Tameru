# Day 23b — MCP server: Claude.ai integration + connected-apps UI

## Goal

End-to-end: a user adds Tameru to Claude.ai (web) as a custom connector, completes an OAuth consent screen Tameru hosts, and asks "how much did I spend on dining last month?" — getting the right number. A "Connected apps" section in Settings lists active connections and revokes them. Depends on Day 23a — the MCP server and OAuth Resource Server must already work.

## Read first

- `DESIGN.md` §7.9 (the full OAuth flow, Tameru-hosted consent, JWT TTL bound), §8.6 (MCP authorization — no dedicated table).
- The Day 23a prompt and the shipped `app/mcp_server.py`.
- `supabase/MCP_OAUTH_SETUP.md` — dashboard config the Day-23a setup left behind; Day-23b extends it.
- `frontend/src/pages/connections.tsx` (the existing fixture-backed "claude connections" page) and `frontend/src/lib/claudeTokens.ts` — both are obsolete; Day-23b replaces/deletes them.
- Supabase OAuth 2.1 Server docs — the user-facing `supabase.auth.oauth.*` methods (`getAuthorizationDetails`, `approveAuthorization`, `denyAuthorization`, `getUserGrants`, `revokeGrant`). All accept the user's session JWT — no service-role bridge needed, invariant 1 stays intact.
- Anthropic Help Center — "Build custom connectors via remote MCP servers."

## Deliverables

- **Frontend — Tameru-hosted consent page:**
  - Supabase's OAuth 2.1 Server does **not** provide a hosted consent UI. The consent screen is a frontend implementation against the user-facing `auth.oauth.*` methods.
  - Add a route `/oauth/consent` in the PWA. (Named `consent`, not `authorize`, to avoid colliding with Supabase's own `{SUPABASE_URL}/oauth/authorize` endpoint that delegates to it.) On load, call `supabase.auth.oauth.getAuthorizationDetails(authorization_id)` (the `authorization_id` arrives as a URL param from Supabase). The response is one of:
    - `OAuthAuthorizationDetails` — show the consent UI.
    - `OAuthRedirect` — user already consented; follow the redirect immediately.
  - Consent UI: render the requesting client's `client_name` + a short read-only reassurance ("Claude.ai will be able to read your transactions, subscriptions, and card multipliers. It cannot add, edit, or delete anything.") + two buttons:
    - "Allow" → `supabase.auth.oauth.approveAuthorization(authorization_id)` → follow the returned redirect.
    - "Cancel" → `supabase.auth.oauth.denyAuthorization(authorization_id)` → follow the returned redirect.
  - No per-scope picker in v1: a valid grant is read-only by construction (MCP exposes only read tools). One Allow, one outcome.
  - The route must be registered as Supabase's authorization path; record the dashboard step in `supabase/MCP_OAUTH_SETUP.md`.

- **Supabase dashboard config — short JWT TTL:**
  - Set **Auth → JWT expiry limit = 300** (5 minutes). Default is 3600 (1 hour). Rationale: revoking an OAuth grant deletes the session + refresh token immediately, but the access JWT Claude.ai already holds is stateless and stays valid until its `exp`. A short TTL is how we bound the residual-access window without adding a per-request server-side session lookup at the MCP layer (which would require service-role access and amend invariant 1). 5 min ⇒ at most ~5 min between Disconnect and Claude.ai losing access.
  - Side effect: the PWA refreshes its session more often. Acceptable.
  - Document the setting + rationale in `supabase/MCP_OAUTH_SETUP.md`.

- **Retire `mcp_tokens`:**
  - A migration in `supabase/migrations/` dropping the `mcp_tokens` table — superseded by Supabase's OAuth 2.1 Server (`DESIGN.md` §8.6). The `20260421120600_mcp_tokens.sql` table is no longer used.
  - Update `tests/contracts/test_rls.py` — drop the `mcp_tokens` row at [line 49] and the `_row_mcp_tokens` helper at [line 353]. Without this, CI breaks the moment the table is gone.
  - Update `AGENTS.md` invariant 3 — currently still says "per-user bearer tokens... stored as `sha256` in `mcp_tokens`." Rewrite to mirror `CLAUDE.md` invariant 3 (OAuth 2.1, Supabase Authorization Server, no Tameru-owned token table).
  - Add a one-line "superseded — see DESIGN.md §8.6" note to the obsolete bullets in `prompt/week-1-foundation/day-02-schema-migrations.md` and `day-03-auth-rls.md`. Don't rewrite the historical prompts.
  - Grep the repo for any remaining `mcp_tokens` / `claudeTokens` references and clear them.

- **Frontend — repurpose `frontend/src/pages/connections.tsx`:**
  - From "generate / copy a bearer token" to **"Connected apps"**: list the user's active OAuth grants and provide a "Disconnect" action.
  - Data source: `supabase.auth.oauth.getUserGrants()`. Uses the user's session JWT directly — no FastAPI bridge, no backend route. Render each grant's `client_name` + the connected-at timestamp.
  - **Do not show a "last used" column.** The grants payload does not expose it, and tracking it would require a Tameru-owned table (which we just retired).
  - "Disconnect" → `supabase.auth.oauth.revokeGrant(client_id)`. This deletes the session and invalidates the refresh token at Supabase; the access token Claude.ai already holds remains valid until its `exp` (≤5 min under the new TTL).
  - Delete `frontend/src/lib/claudeTokens.ts` entirely (`initialTokens`, `generateTokenSecret`, the `ClaudeToken` type) and the `RevealSheet` component in `connections.tsx`; nothing in the OAuth model maps to them.
  - Show instructions for adding Tameru as a custom connector in Claude.ai — the MCP URL only. Claude.ai drives the OAuth flow; there is no token to paste.
  - Do not hardcode `tameru.app` as the MCP URL — derive the backend host from `VITE_API_BASE_URL` and append `/mcp`.
  - Update the `claudeConnected` chip on `frontend/src/pages/more.tsx` to derive from `getUserGrants()` (currently fixture-driven via `initialTokens`).

- **Tests:**
  - **Automated** (`tests/test_mcp.py`): seed a user, take their session JWT, call `auth.oauth.revokeGrant(...)` against local Supabase, and assert a subsequent MCP tool call carrying a same-user access token fails after the session is gone (within local-Supabase JWT TTL). If local Supabase keeps the stateless JWT alive until `exp`, gate the assertion on `exp`-based expiry and document the limitation in a code comment — the production behavior the user sees is bounded by the 5-min TTL setting, not the test fixture.
  - **Automated**: assert `getUserGrants()` returns the freshly-approved grant; `revokeGrant(client_id)` removes it.
  - **UAT (manual, Day-28 style):** end-to-end against the real Claude.ai web client — add the MCP URL, complete consent on `/oauth/consent`, ask a real spending question, then Disconnect from Settings → Connected apps and confirm Claude.ai is locked out within ~5 minutes.

## Doc sync (same change)

- `DESIGN.md` §7.9 — add the "Tameru hosts the consent screen at `/oauth/consent`" sentence and the "JWT TTL = 5 min bounds the post-revocation residual window" rationale. §8.6 should still match shipped behavior; verify and correct any drift.
- `CLAUDE.md` invariant 3 already reflects OAuth; invariant 1 was confirmed intact during Day 23a — no change needed unless something drifted.
- `AGENTS.md` invariant 3 — see the cleanup deliverable above.

## Don't

- Don't reintroduce a Tameru-owned token table — auth state lives in Supabase's OAuth Server.
- Don't expose any mutating tool over MCP (`CLAUDE.md` invariant 3).
- Don't build a FastAPI bridge for grant listing/revocation — the user-facing `auth.oauth.*` methods accept session JWTs directly. A bridge would force service-role access for nothing.
- Don't leave `DESIGN.md` / `CLAUDE.md` / `AGENTS.md` lagging the shipped behavior.
- Don't try to revoke the in-flight access token itself — JWTs are stateless. The TTL + refresh-token revocation is the v1 mechanism.

## Done when

- **Automated:** `mcp_tokens` table is dropped, `tests/contracts/test_rls.py` no longer references it, and `tests/test_mcp.py` covers the revoke-then-tool-call path described above.
- **Automated:** `getUserGrants()` lists at least one connection after a successful consent; `revokeGrant()` removes it.
- **Automated:** the `/oauth/consent` route renders, calls `getAuthorizationDetails`, and wires Allow/Cancel to `approveAuthorization`/`denyAuthorization` (component-level test).
- **UAT (manual):** adding the Tameru MCP URL to Claude.ai (web) as a custom connector, completing OAuth consent on Tameru's consent page, then asking "how much did I spend on dining last month?" returns the right number.
- **UAT (manual):** Settings → Connected apps lists the connection; tapping "Disconnect" blocks Claude.ai from getting new answers within ~5 minutes (refresh-token gone, access-token expiry bounded by the JWT TTL config).
