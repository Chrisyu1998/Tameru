# Day 23b — MCP server: Claude.ai integration + connected-apps UI

## Goal

End-to-end: a user adds Tameru to Claude.ai (web) as a custom connector, completes an OAuth consent screen, and asks "how much did I spend on dining last month?" — getting the right number. A "Connected apps" section in Settings lists active connections and revokes them. Depends on Day 23a — the MCP server and OAuth Resource Server must already work.

## Read first

- `DESIGN.md` §7.9 (the full OAuth flow), §8.6 (MCP authorization — no dedicated table).
- The Day 23a prompt and the shipped `app/mcp_server.py`.
- `frontend/src/pages/connections.tsx` — the existing "claude connections" page (currently fixture-backed) and `frontend/src/lib/claudeTokens.ts`.
- Anthropic Help Center — "Build custom connectors via remote MCP servers."

## Deliverables

- **OAuth consent flow:**
  - Verify the Supabase OAuth Server consent screen renders acceptably, branded as Tameru where Supabase allows it. If a custom consent surface is genuinely required, build the minimum — do not gold-plate.
- **Retire `mcp_tokens`:**
  - A migration in `supabase/migrations/` dropping the `mcp_tokens` table — superseded by Supabase's OAuth 2.1 Server (`DESIGN.md` §8.6). The `20260421120600_mcp_tokens.sql` table is no longer used.
  - Confirm no code references `mcp_tokens`.
- **Frontend — repurpose `frontend/src/pages/connections.tsx`:**
  - From "generate / copy a bearer token" to **"Connected apps"**: list the user's active OAuth grants (client name, last used) with a "Disconnect" action.
  - "Disconnect" revokes the OAuth grant at the Supabase authorization server.
  - Remove the fixture path (`initialTokens`, `generateTokenSecret` in `lib/claudeTokens.ts`); wire to real data.
  - Show instructions for adding Tameru as a custom connector in Claude.ai — the MCP URL only. Claude.ai drives the OAuth flow; there is no token to paste.
  - Do not hardcode `tameru.app` as the MCP URL — resolve the backend host from config.
- **Tests:**
  - Extend `tests/test_mcp.py`: completing the OAuth flow yields a working connection; revoking the grant blocks subsequent tool calls (`401`).

## Doc sync (same change)

- `DESIGN.md` §7.9 and §8.6 were updated to the OAuth model during the Day 23 review. Verify they still match what shipped; correct any drift.
- `CLAUDE.md` invariant 3 already reflects OAuth. If Day 23a took the service-role path, invariant 1 must also have been amended there.

## Don't

- Don't reintroduce a Tameru-owned token table — auth state lives in Supabase's OAuth Server.
- Don't expose any mutating tool over MCP (`CLAUDE.md` invariant 3).
- Don't leave `DESIGN.md` / `CLAUDE.md` lagging the shipped behavior.

## Done when

- Adding the Tameru MCP URL to Claude.ai (web) as a custom connector, completing OAuth consent, then asking "how much did I spend on dining last month?" returns the right number.
- Settings → Connected apps lists the connection; "Disconnect" immediately blocks Claude.ai from getting answers.
- `mcp_tokens` is dropped and no code references it.
