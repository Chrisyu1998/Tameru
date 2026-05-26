# Day 27 — Privacy disclosures, Anthropic ZDR, CSP/CORS lockdown, data export

## Goal

Match the privacy promises in `DESIGN.md` §9.4 to reality. Anthropic ZDR requested. Settings shows the user-facing disclosure copy. CSP and CORS locked down. `/export` endpoint dumps user data on demand.

## Read first

- `DESIGN.md` §9 (full security & privacy section).

## Deliverables

- **Anthropic ZDR:** submit the request via the Anthropic Console. Document the request date and contact email in `docs/zdr_request.md` (a new file). Until ZDR is granted, the privacy copy reads "default 30-day Anthropic trust & safety retention; ZDR requested."
- **CSP:** add to FastAPI middleware:
  - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' <SUPABASE_URL> https://us.i.posthog.com https://us-assets.i.posthog.com <SENTRY_INGEST>; frame-ancestors 'none';`
  - PostHog hosts pinned to **US Cloud** — matches `VITE_POSTHOG_HOST` in `.env.example`. EU Cloud + GDPR-consent flow is a §17 deliverable, not v1 (see Day 26 "Region" section).
  - Test via curl + Lighthouse audit.
- **CORS:** FastAPI CORS middleware allows only the production origin (e.g. `https://tameru.app`) and `http://localhost:5173` for dev. No wildcards.
- **Data export:**
  - `GET /export` — returns a single JSON file: `{user_id, exported_at, transactions: [...], cards: [...], subscriptions: [...], memory: [...]}`. RLS-scoped. (No MCP credentials: there is no `mcp_tokens` table — MCP auth is delegated to Supabase's OAuth 2.1 Server, DESIGN.md §7.9.)
  - In Settings: "Export my data" button → triggers a download.
  - Also wire as a chat tool: a new `export_data()` typed tool that returns the export and a download URL valid for 5 minutes (use Supabase Storage signed URL or a one-shot token).
- **Settings → Privacy section:**
  - **AI provider disclosure copy** from `DESIGN.md` §9.4, verbatim (Anthropic ZDR-requested + Gemini paid-tier-no-training language).
  - **PostHog disclosure copy** from `DESIGN.md` §9.5 — name the events that are tracked (the whitelist), name the things that are never tracked ("transaction amounts, merchant names, card details, question text"), name the region ("PostHog US Cloud"). One short paragraph, not a wall of text.
  - Opt-out toggle for analytics (already from Day 26 — this prompt adds the disclosure prose **around** the toggle, not the toggle itself).
  - "Export my data" button.
  - "Delete my account" button (Phase 2 — link out to email for now).
- **README** — add a "Privacy" section pointing to Settings + the disclosure copy (both §9.4 and §9.5).

## Don't

- Don't loosen CSP or CORS to fix a missing asset — fix the asset reference instead.
- Don't claim ZDR is active before Anthropic confirms. Update the copy when it lands.

## Done when

- A real curl returns the CSP header. A Lighthouse "Best practices" audit doesn't flag CSP issues.
- A cross-origin fetch from a non-allowed domain is rejected by CORS.
- "Export my data" produces a valid JSON file with exactly the user's own data.
- The privacy copy in Settings matches DESIGN.md §9.4 (AI providers) **and** §9.5 (PostHog scope + region).
- The ZDR request is filed and dated.
