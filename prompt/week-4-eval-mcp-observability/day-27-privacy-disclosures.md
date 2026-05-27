# Day 27 — Privacy disclosures, Anthropic ZDR, CSP/CORS lockdown, data export

## Goal

Match the privacy promises in `DESIGN.md` §9.4–§9.6 to reality. Anthropic ZDR requested (not yet active). CSP locked down at the Vercel edge; JSON-API hardening headers on FastAPI. CORS verified and pinned by a contract test. Disclosure prose and an "Export my data" button shipped on both `/privacy` (mobile) and Settings → Privacy (desktop). `GET /export` dumps the user's own data as JSON, RLS-scoped.

## Read first

- `DESIGN.md` §9 (full security & privacy section), §6.4 (digest Reply-To = `hello@mail.tameru.xyz`, the support inbox used below).
- `CLAUDE.md` invariant 1 — `/export` runs under the user's JWT, never service-role.
- `CLAUDE.md` invariant 8 — read-only, but explains why there is no `export_data()` chat tool.
- [app/main.py](app/main.py) `_cors_allowed_origins` (L189-L202) — CORS allowlist already implemented; this prompt only pins it with a test.
- Day 26 `AnalyticsOptOutToggle` shared-component pattern (memory 2026-05-26) — disclosure prose follows the same dual-surface shape.

## Deliverables

### Anthropic ZDR — request + artifact

- File the ZDR request via Anthropic Console → Settings → Privacy ("Request Zero Data Retention"). If the toggle isn't surfaced for the org tier, email `privacy@anthropic.com` with org id + use case.
- Create `docs/zdr_request.md` (local-only, gitignore-ok if it contains PII):
  - Filing date.
  - Contact email used.
  - Request method (Console vs. email).
  - Expected response window (Anthropic's published SLA, currently "a few business days").
  - Follow-up cadence: re-ping at 2 weeks if no response.
- In-app privacy copy stays hedged until Anthropic confirms: *"default 30-day Anthropic trust & safety retention; ZDR requested."* Update copy in the same PR that records the confirmation.
- **Also update `DESIGN.md` §9.4's disclosure block quote** — its current "Both providers are configured for no data retention" wording is more optimistic than §9.4 paragraph 1 ("ZDR requested"). The two must match; the hedged version is the truthful one.

### CSP — Vercel only, not FastAPI

CSP protects the document origin. Tameru's documents are served by Vercel (`tameru.xyz`); the FastAPI backend serves only JSON to a cross-origin `Authorization`-bearing client. CSP on the API would protect nothing the browser cares about.

Add a `headers` block to `frontend/vercel.json` applying to `/(.*)`:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  font-src 'self' data:;
  connect-src 'self'
    https://<supabase-project-ref>.supabase.co
    wss://<supabase-project-ref>.supabase.co
    https://api.tameru.xyz
    https://us.i.posthog.com
    https://us-assets.i.posthog.com
    https://*.ingest.us.sentry.io;
  worker-src 'self';
  manifest-src 'self';
  frame-ancestors 'none';
  base-uri 'self';
  form-action 'self';
```

- PostHog hosts pinned to **US Cloud** (matches `VITE_POSTHOG_HOST`). EU is a §17 deliverable.
- Sentry browser SDK ingest is region-scoped at `*.ingest.us.sentry.io`.
- Supabase host includes `wss://` for Realtime channels in case they're added post-launch; harmless if unused.
- `worker-src 'self'` for the Service Worker (DESIGN.md §10.1).
- `manifest-src 'self'` for the Web App Manifest (PWA install).

On the FastAPI side, add a minimal hardening middleware in [app/main.py](app/main.py) for the JSON API — these matter even without HTML:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`

No CSP on FastAPI.

### CORS — verify + pin, don't rebuild

The allowlist is already in [app/main.py:189-202](app/main.py#L189-L202). Add `tests/contracts/test_cors_allowlist.py` asserting:
- In `APP_ENV=production` with `FRONTEND_ORIGIN=https://tameru.xyz`, `_cors_allowed_origins()` returns exactly `{"https://tameru.xyz", "http://localhost:5173"}`.
- No element matches `*`, no element matches `*.vercel.app`, no element contains the substring `vercel.app`.

### Data export

**Endpoint:** new `app/routes/export.py` with `GET /export`. JWT-authenticated, RLS-scoped, no service role. Returns one JSON object:

```jsonc
{
  "user_id": "uuid",
  "exported_at": "2026-05-26T14:23:01Z",
  "schema_version": 1,
  "transactions": [/* all rows */],
  "cards": [/* all rows incl. soft-deleted */],
  "subscriptions": [/* all rows incl. paused/cancelled */],
  "user_memory": [/* all facts */],
  "chat_messages": [/* full history */],
  "merchant_category": [/* user's category overrides */],
  "users_meta": { /* single row */ }
}
```

**v1 scope rationale (greppable in the route docstring):** include every table containing user content or user preferences. Excluded from v1, deferred to a future full-audit export only if asked:
- `chat_turn_trace` — agent-loop tool-call audit
- `ai_call_log`, `ai_call_log_daily` — AI cost/audit trail
- `email_log` — Resend send/bounce/complaint log

**Download mechanism — direct in-browser Blob, no signed URL, no token:**
- Settings / privacy "Export my data" button calls `fetch('/export', { headers: { Authorization: 'Bearer <jwt>' } })`.
- Frontend wraps the JSON response in a `Blob`, creates an object URL, triggers a synthetic `<a download="tameru-export-{YYYY-MM-DD}.json">` click, revokes the URL.
- The user's JWT is the auth; no Supabase Storage, no `EXPORT_TOKEN_SECRET`, no `/export/download` endpoint.

**No agent tool.** An `export_data()` typed tool is read-only (so it wouldn't trip invariant 8's write rule), but it adds a new agent surface that bypasses the Settings affordance, costs an `ai_call_log` row per "export my data" utterance, and forces a download-URL mechanism for the link the agent renders. If a chat user asks how to export, the agent answers in prose pointing to Settings.

### Settings → Privacy + `/privacy` — shared disclosure component

Day 26's dual-surface pattern (memory 2026-05-26 "AnalyticsOptOutToggle extracted into a shared component") applies again. Disclosure prose, "Export my data" button, and the deferred "Delete my account" mailto are all shared, rendered on both surfaces.

- New `frontend/src/components/PrivacyDisclosure.tsx` — two short paragraphs:
  - **AI providers** — copy from `DESIGN.md` §9.4 (using the hedged wording from §9.4 paragraph 1, NOT the optimistic block quote). Names Anthropic + Gemini, ZDR-requested status, Gemini paid-tier-no-training.
  - **Analytics** — copy derived from `DESIGN.md` §9.5. Names the events tracked (the whitelist), the things never tracked ("transaction amounts, merchant names, card details, question text"), and the region ("PostHog US Cloud").
- New `frontend/src/components/ExportDataButton.tsx` — owns the `fetch` + Blob + anchor-click logic; reports `feature_used` PostHog event with subtype `data_export`.
- New `frontend/src/components/DeleteAccountRow.tsx` — renders a row that opens `mailto:hello@mail.tameru.xyz?subject=Delete%20my%20account&body=...` with helper copy *"Email us — we'll delete your account within 7 days."* (Phase 2 work per `DESIGN.md` §17.11; in-app button deferred.)
- Render order on both surfaces: `AnalyticsOptOutToggle` → `PrivacyDisclosure` → `ExportDataButton` → `DeleteAccountRow`.
- Surfaces to edit:
  - [frontend/src/pages/privacy.tsx](frontend/src/pages/privacy.tsx) — replace the L7-L9 placeholder stub copy with the new component stack.
  - [frontend/src/pages/settings.tsx](frontend/src/pages/settings.tsx) Privacy section (L397-L409) — add the same component stack below the existing toggle.

### README

Replace any §9.4/§9.5 cross-reference with a one-line "Privacy" section pointing users to **in-app Settings → Privacy** for the live disclosure. A public Privacy Policy document is §17 scaling-phase work.

## Don't

- Don't put CSP on the FastAPI backend — it protects the document origin (Vercel), not a cross-origin JSON API.
- Don't loosen CSP or CORS to fix a missing asset — fix the asset reference instead.
- Don't claim ZDR is active before Anthropic confirms; update the copy in the PR that records the confirmation, and update `DESIGN.md` §9.4 in the same change.
- Don't introduce a Supabase Storage bucket for export downloads — in-browser Blob + synthetic-anchor click is sufficient at v1 scale.
- Don't add an `export_data()` chat tool — bypasses the Settings affordance for no UX gain.
- Don't include `chat_turn_trace`, `ai_call_log`, `ai_call_log_daily`, or `email_log` in the v1 export — those are internal observability records, not user data.
- Don't reach for `supabase_admin` in the `/export` handler — the user's JWT is in scope; RLS does the filtering. (Invariant 1.)

## Done when

- `curl -I https://tameru.xyz/` returns the CSP header with the directive set above (verified at the Vercel edge, not Railway).
- `curl -I https://api.tameru.xyz/healthz` returns `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and **no** `Content-Security-Policy`.
- A Lighthouse "Best practices" audit on the deployed PWA reports no CSP violations.
- `curl -i -X OPTIONS https://api.tameru.xyz/dashboard/summary -H "Origin: https://evil.example" -H "Access-Control-Request-Method: GET"` returns no `Access-Control-Allow-Origin` header.
- `tests/contracts/test_cors_allowlist.py` passes.
- "Export my data" on both `/privacy` and Settings → Privacy downloads a file named `tameru-export-{YYYY-MM-DD}.json` containing exactly the seven keys listed above for that user, and nothing else; unauthenticated `GET /export` returns 401.
- The disclosure prose in both surfaces matches `DESIGN.md` §9.4 (AI providers, hedged ZDR-requested wording) and §9.5 (PostHog scope + region).
- `docs/zdr_request.md` exists with filing date, contact email, request method, expected response window, and the 2-week follow-up cadence.
- "Delete my account" row opens a `mailto:hello@mail.tameru.xyz` link with the subject prefilled.
- `DESIGN.md` §9.4 block-quote wording is reconciled with paragraph 1's hedged "ZDR requested" stance.
