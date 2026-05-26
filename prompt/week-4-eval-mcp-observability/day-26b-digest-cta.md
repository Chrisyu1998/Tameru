# Day 26b — Digest CTA + `weekly_digest_opened` on PWA landing

## Goal

Wire the `weekly_digest_opened` PostHog event (Day 26) to a real signal. The digest email gains a "View this week in Tameru" CTA pointing at `${FRONTEND_ORIGIN}/?source=digest`; the PWA fires the event on landing when the query param is present, then strips it. No Resend open-pixel, no server-side PostHog, no new webhook.

## Read first

- `DESIGN.md` §6.4 (digest content + brevity rules — this prompt adds a CTA without breaching the ≤5-content-block ceiling).
- `prompt/week-4-eval-mcp-observability/day-25-weekly-digest.md` §4 "Compose + render" (the `render_email` signature this prompt amends).
- `prompt/week-4-eval-mcp-observability/day-26-posthog.md` (the `track()` wrapper this prompt consumes — `weekly_digest_opened` is already in the whitelist; only the firing surface changes).
- `memory.md` 2026-05-22 "Sending real email from a domain you control" (the rationale for *not* using the open-pixel approach).

## Background

Day 26 originally proposed firing `weekly_digest_opened` from a Resend open-tracking pixel via `POST /webhooks/resend/opened`. That violates DESIGN.md §6.4 ("Resend's open and click tracking are **disabled** in project settings") and CLAUDE.md privacy posture (third-party pixel exfiltrates recipient IP on every email open). The click-back-into-PWA approach is also what DESIGN.md §16 line 1588 implies ("measure via PostHog `weekly_digest_opened` after Phase 1 launch").

This prompt makes that switch implementable: the digest email today has only an Unsubscribe link, no link back into the app, so there is no current surface for the event to fire on regardless of mechanism. One small render-side edit + one PWA landing handler closes the loop.

## Deliverables

### 1. Digest CTA (amends Day 25 §4 "Compose + render")

`app/services/digest.py`:

- `render_email(payload, unsubscribe_url, *, app_cta_url) -> RenderedEmail` — gains a third required kwarg. Caller supplies `f"{FRONTEND_ORIGIN}/?source=digest"`.
- HTML version: a button-styled `<a>` placed **after** the prose content blocks and **before** the unsubscribe footer. Inline-styled (Gmail strips classes), single dark-on-light pill, label `View this week in Tameru`. Not counted as a "content block" — it is a navigation affordance, structurally distinct from the prose. Block count rule from DESIGN.md §6.4 still ≤5 prose blocks (total, top-category, observation, optional nudge, unsubscribe note).
- Plaintext version: appended as `View this week in Tameru: {app_cta_url}` on its own line, **above** the `Unsubscribe: …` line.
- The link goes to **`FRONTEND_ORIGIN`** (Vercel PWA host — `app/main.py` already reads this var for CORS), **not** `BACKEND_PUBLIC_URL` (which `_unsubscribe_urls` uses because `/unsubscribe` is a FastAPI route). Wrong env var here would land the user on a 404.
- The query param is exactly `?source=digest` — match Day 25's convention of plaintext param names; no UTM hierarchy in v1.

`app/cron/digest.py`:

- Read `FRONTEND_ORIGIN` at module level (fail-loud if unset — add to `_REQUIRED_ENV_VARS` in `app/main.py` if not already there). Pass `f"{FRONTEND_ORIGIN}/?source=digest"` to `render_email`.
- No per-user token in the URL. The event carries no identity; the page that fires it does so under the authenticated PWA session (or anonymous if the user isn't signed in on this device), which is the right semantic.

### 2. PWA landing handler (consumes Day 26 `track()`)

`frontend/src/lib/analytics.ts` (or a small sibling, `frontend/src/lib/digestLanding.ts`):

- `initDigestLandingTracking()` — called once at app bootstrap (e.g. from `frontend/src/main.tsx` or `App.tsx`, after `analytics.ts` init).
- Behavior: on first render, read `URLSearchParams(window.location.search)`; if `source === 'digest'`, call `track('weekly_digest_opened', {})`, then strip the param via `window.history.replaceState({}, '', window.location.pathname + remainingParams)`.
- Runs **after** the PostHog opt-out check has resolved (deliverable from Day 26 §2 of the recommendations — extend `/me` to return `analytics_opted_out`; init PostHog with `opt_out_capturing_by_default: true`). An opted-out user reads `source=digest`, the param is stripped, `track()` no-ops — same fall-through path as any other event.
- Fires exactly once per landing — guard with a module-level flag so a hot-reload or a `useEffect` re-mount doesn't re-fire. Stripping the param is the structural guard (URLSearchParams won't match on the next render), but the in-memory flag closes the StrictMode double-mount window.

### 3. DESIGN.md sync (same commit)

- §6.4 — add one line to the digest content description: "The email includes a 'View this week in Tameru' button linking to `${FRONTEND_ORIGIN}/?source=digest`. The PWA fires `weekly_digest_opened` (§9.5 whitelist) on landing when the param is present, then strips it. No open-tracking pixel."
- §9.5 — clarify that `weekly_digest_opened` is fired client-side from the PWA landing, not from Resend. Drop any implicit "open pixel" interpretation.

## Don't

- Don't add `?source=digest` as a UTM-prefixed param (`?utm_source=digest`). Day 25 uses plaintext param names; consistency.
- Don't sign or HMAC the param — there's no identity claim to forge; `weekly_digest_opened` carries no `user_id` in `props` per Day 26's whitelist.
- Don't link the CTA at `BACKEND_PUBLIC_URL` — that's the Railway backend; the digest CTA lands the user on the PWA.
- Don't enable Resend open or click tracking in the dashboard. Day 25 §Prerequisites already pins this; restating because this prompt is the natural place a future reader might be tempted to re-enable open tracking "since we have a use case now."
- Don't re-fire `weekly_digest_opened` if the user navigates within the SPA after landing — the strip-param + module-level guard prevents this. Verify with a quick navigate-away-and-back test.
- Don't gate the CTA on the user being signed in. An anonymous-device click still fires the event with PostHog's anonymous `distinct_id`; if the user then signs in, PostHog's `identify` reconciles the anonymous events. This is the intended PostHog flow.

## Done when

- `python -m app.cron.digest --user <test_user_id> --dry-run` prints an HTML body whose source contains `<a href="${FRONTEND_ORIGIN}/?source=digest"` styled as a button, and a plaintext body whose lines include `View this week in Tameru: ${FRONTEND_ORIGIN}/?source=digest` above the `Unsubscribe:` line.
- A received digest email, when its CTA is tapped, lands the user on the PWA at `/` with no `?source=digest` in the visible URL after first paint.
- PostHog's live events view shows exactly one `weekly_digest_opened` event per CTA tap from a non-opted-out user, with the expected `distinct_id` (or anonymous id if the user isn't signed in on that device).
- A CTA tap from an opted-out user produces zero PostHog events (verified by network inspection — no request to `us.i.posthog.com`).
- Day 25's `tests/test_compose_digest.py` extended (or a sibling test added) to assert: (a) the rendered HTML contains the CTA href; (b) the rendered plaintext contains the CTA line; (c) `render_email` raises `TypeError` if `app_cta_url` is omitted (kwarg-only, required).
- DESIGN.md §6.4 and §9.5 reflect the CTA + landing-handler approach in the same commit.
