# Day 26b — Digest CTA + `weekly_digest_opened` on PWA landing

## Goal

Wire the `weekly_digest_opened` PostHog event (Day 26) to a real signal. The digest email gains a "View this week in Tameru" CTA pointing at `${FRONTEND_ORIGIN}/?source=digest`; the PWA fires the event on landing when the query param is present, then strips it. No Resend open-pixel, no server-side PostHog, no new webhook.

## Read first

- `DESIGN.md` §6.4 (digest content + brevity rules — this prompt adds a CTA without breaching the ≤5-content-block ceiling).
- `prompt/week-4-eval-mcp-observability/day-25-weekly-digest.md` §4 "Compose + render" (the `render_email` signature this prompt amends).
- `prompt/week-4-eval-mcp-observability/day-26-posthog.md` (the `track()` wrapper this prompt consumes — `weekly_digest_opened` is already in the whitelist; only the firing surface changes).
- `memory.md` 2026-05-22 "Sending real email from a domain you control" (the rationale for *not* using the open-pixel approach).
- `memory.md` 2026-05-25 "Weekly digest cron is a separate Railway service" — load-bearing for §1: the cron does NOT enter `app/main.py`'s `lifespan`, so its env-var fail-loud lives at the cron's module load.

## Depends on

- **Day 26 merged first.** The `track()` wrapper, `/me.analytics_opted_out`, and the `weekly_digest_opened` literal in the [Event union](frontend/src/lib/analytics.ts#L83) all ship in Day 26. As of 2026-05-26 Day 26 is merged (`5d496bd`); this prompt has no remaining ordering risk.

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

- **Fail-loud at the cron's module load** — the cron runs as a separate Railway service (`digest-cron`) whose start command never enters `app/main.py`'s `lifespan`, so `_REQUIRED_ENV_VARS` does not protect it. At module top:
  ```python
  FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "").rstrip("/")
  if not FRONTEND_ORIGIN:
      raise RuntimeError("FRONTEND_ORIGIN is not set for the digest cron.")
  ```
  The `.rstrip("/")` is load-bearing: a value with a trailing slash produces `https://x.xyz//?source=digest`, a different URL the PWA's landing handler still matches but readers (and any URL-equality tests) will not.
- **Also** add `"FRONTEND_ORIGIN"` to `_REQUIRED_ENV_VARS` in `app/main.py` — the web service reads it in `_cors_allowed_origins()` and currently boots silently when it's unset. Two services, two boot-time checks; same env var.
- Pass `f"{FRONTEND_ORIGIN}/?source=digest"` to `render_email`.
- No per-user token in the URL. The event carries no identity; the page that fires it does so under the authenticated PWA session — see §2 below for the anonymous-device case (accepted measurement gap).

### 2. PWA landing handler (consumes Day 26 `track()`)

`frontend/src/lib/analytics.ts` (or a small sibling, `frontend/src/lib/digestLanding.ts`):

- `initDigestLandingTracking()` — called from `App`'s existing `useEffect` in [main.tsx:56-78](frontend/src/main.tsx#L56-L78), **chained inside the `initAuth().then(...)` that already sets `authReady`**. This is the load-bearing call-site contract: `initAuth → refreshHomeCurrency → setOptOut(me.analytics_opted_out)` is what flips the PostHog SDK out of opted-out-by-default ([analytics.ts:121](frontend/src/lib/analytics.ts#L121)); calling the landing handler any earlier than that means `track()` no-ops for opted-in users on a cold landing. Order inside the `.then`: `setAuthReady(true)` → `initDigestLandingTracking()` → `startDeviceCheckPoll()` / `setupAutoDrain()`.
- Behavior: read `URLSearchParams(window.location.search)`; if `source === 'digest'`, call `track('weekly_digest_opened', {})`, then strip the param by building a new query string from the remaining params and calling `window.history.replaceState({}, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)` (preserve hash for any future deep links; preserve other query params for any future co-existing source values).
- **Anonymous-device clicks are an accepted measurement gap.** A user without a Supabase session on this device hits `RequireOnboarded` ([main.tsx:44-50](frontend/src/main.tsx#L44-L50)) and gets `<Navigate to="/onboarding" replace>` — which does not preserve query params. Even if the landing handler ran before Navigate, `setOptOut(false)` never runs without a session (auth.ts:194 takes the no-session branch and skips `/me`), so PostHog stays opted-out-by-default and `track()` is a no-op. We do not work around either of these: invariant 5 (single active device per user) means most digest taps come from the device that already holds the session, and the underreport is a constant fraction that doesn't bias week-over-week trends. **Do not** attempt to localStorage-stash the click for replay after sign-in, or to opt PostHog in for anonymous users — both would violate Day 26's leak-free-init invariant for a measurement gain that doesn't gate any Phase 1 decision.
- Fires exactly once per landing — guard with a module-level flag so a hot-reload or a `useEffect` re-mount doesn't re-fire. Stripping the param is the structural guard (URLSearchParams won't match on the next render), but the in-memory flag closes the StrictMode double-mount window.

### 3. DESIGN.md sync (same commit)

- §6.4 — add two lines to the digest content description: (1) "The email includes a 'View this week in Tameru' button linking to `${FRONTEND_ORIGIN}/?source=digest`. The PWA fires `weekly_digest_opened` (§9.5 whitelist) on landing when the param is present, then strips it. No open-tracking pixel." (2) "The CTA button is a navigation affordance, not a prose block; the ≤5 prose-block ceiling still applies to the prose region above it." Without the second line, the next reader hits the same ambiguity the prompt resolves by fiat.
- §9.5 — clarify that `weekly_digest_opened` is fired client-side from the PWA landing handler, not from Resend, and is gated on the user being signed in on the landing device (anonymous-device clicks are an accepted measurement gap at v1 scale, per the §2 reasoning). Drop any implicit "open pixel" interpretation.

## Don't

- Don't add `?source=digest` as a UTM-prefixed param (`?utm_source=digest`). Day 25 uses plaintext param names; consistency.
- Don't sign or HMAC the param — there's no identity claim to forge; `weekly_digest_opened` carries no `user_id` in `props` per Day 26's whitelist.
- Don't link the CTA at `BACKEND_PUBLIC_URL` — that's the Railway backend; the digest CTA lands the user on the PWA.
- Don't enable Resend open or click tracking in the dashboard. Day 25 §Prerequisites already pins this; restating because this prompt is the natural place a future reader might be tempted to re-enable open tracking "since we have a use case now."
- Don't re-fire `weekly_digest_opened` if the user navigates within the SPA after landing — the strip-param + module-level guard prevents this. Verify with a quick navigate-away-and-back test.
- Don't try to make anonymous-device clicks fire (localStorage-stash-then-replay-after-sign-in, opt-in-PostHog-for-anonymous-users, custom `distinct_id` minting). All three violate Day 26's leak-free-init invariant for a measurement gain that doesn't move any Phase 1 decision. The accepted gap is documented in §2 and in DESIGN.md §9.5.
- Don't trim or alter `FRONTEND_ORIGIN` outside the cron's module-level normalization. `app/main.py`'s `_cors_allowed_origins()` reads it raw — CORS origin matching is exact-string against the browser's `Origin` header (which never has a trailing slash), so if CORS works today the env value is already clean. Don't add a second normalization site that could drift.

## Done when

- `python -m app.cron.digest --user <test_user_id> --dry-run` prints an HTML body whose source contains `<a href="${FRONTEND_ORIGIN}/?source=digest"` styled as a button, and a plaintext body whose lines include `View this week in Tameru: ${FRONTEND_ORIGIN}/?source=digest` above the `Unsubscribe:` line.
- A received digest email, when its CTA is tapped from a device with an active session, lands the user on the PWA at `/` with no `?source=digest` in the visible URL after first paint.
- PostHog's live events view shows exactly one `weekly_digest_opened` event per CTA tap from a signed-in, non-opted-out user, with the expected `distinct_id`. An anonymous-device tap produces zero PostHog events (this is the accepted gap, not a regression).
- A CTA tap from an opted-out user produces zero PostHog events (verified by network inspection — no request to `us.i.posthog.com`).
- A boot of `app/cron/digest.py` with `FRONTEND_ORIGIN` unset (or empty, or whitespace-only) raises `RuntimeError` before reading any user rows. Same for `uvicorn app.main:app` (covered by `_REQUIRED_ENV_VARS`).
- Day 25's `tests/test_compose_digest.py` extended (or a sibling test added) to assert: (a) the rendered HTML contains the CTA href; (b) the rendered plaintext contains the CTA line; (c) `render_email` raises `TypeError` if `app_cta_url` is omitted (kwarg-only, required); (d) a `FRONTEND_ORIGIN` value passed in with a trailing slash produces a clean `/?source=digest` URL (no double-slash).
- Frontend vitest pins the once-only-firing invariant — using `analytics._testing.forceEnabled(true)` and a mocked `posthog-js`, set `window.location.search = '?source=digest'`, call `initDigestLandingTracking()` twice (StrictMode shape), assert `posthog.capture` was called once with `'weekly_digest_opened'` and `history.replaceState` was called once with the param removed.
- DESIGN.md §6.4 and §9.5 reflect the CTA + landing-handler approach (and the prose-block carve-out, and the anonymous-gap rationale) in the same commit.
