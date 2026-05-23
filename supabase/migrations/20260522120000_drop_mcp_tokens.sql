-- Day 23b — drop mcp_tokens (DESIGN.md §8.6).
--
-- The original per-user bearer-token design (migration
-- 20260421120600_mcp_tokens.sql) is superseded by OAuth 2.1 via Supabase
-- Auth's OAuth 2.1 Server: Tameru's MCP server is now an OAuth Resource
-- Server (DESIGN.md §7.9), so Tameru owns no MCP credential storage.
-- Registered clients, grants, and tokens live inside Supabase Auth.
--
-- No data preservation: the table was never populated in any environment
-- (Day 23 planning replaced bearer tokens with OAuth before launch).

DROP TABLE IF EXISTS mcp_tokens;
