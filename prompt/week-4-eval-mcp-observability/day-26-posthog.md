# Day 26 — PostHog product analytics (structural events only)

## Goal

Track which features are used and how often. Zero financial data. Zero question text. User can opt out. PostHog SDK is initialized opted-out and only opts in after the server confirms the user's preference — no leak window on cold load.

## Read first

- `DESIGN.md` §9.5 (PostHog scope — read carefully), §6.4 (digest CTA mechanism — see Day 26b).
- `CLAUDE.md` "Privacy posture" (line 83 — no question classifier, no user content in PostHog events) and invariant 8 (chat is the only user-initiated create surface in v1 — `feature_used: 'manual_entry'` from DESIGN.md §9.5's enum is reserved for the post-Phase-1 separate-form path and does not fire in v1).
- `prompt/week-4-eval-mcp-observability/day-25-weekly-digest.md` §9 "Frontend Settings toggle" (the existing `PATCH /me/preferences` pattern this prompt extends) and `frontend/src/lib/preferencesApi.ts` (the read/write helper — its TODO about extending `/me` is what this prompt resolves).
- `prompt/week-4-eval-mcp-observability/day-26b-digest-cta.md` (the firing surface for `weekly_digest_opened` — no Resend pixel, no server-side PostHog).

## Region

Pin **PostHog US Cloud** — `VITE_POSTHOG_HOST=https://us.i.posthog.com` (already in `.env.example`). v1's user base is invite-only friends/family centered in the Americas (DESIGN.md §3.3, §6.4 line 441); no EU users. EU Cloud + GDPR-consent disclosure is a §17 scaling-plan deliverable (`DESIGN.md` line 1683), not v1.

## Deliverables

### 1. Backend — extend `/me` and `/me/preferences`

- `app/main.py` `GET /me`: extend the returned payload from `{user_id, email, home_currency}` to `{user_id, email, home_currency, analytics_opted_out, weekly_digest_enabled}`. Both new fields read from the same `users_meta` row already in scope; defaults are `false` and `true` respectively. The `frontend/src/lib/preferencesApi.ts` doc-comment already flags this as the right shape ("would need to start returning preferences too").
- `app/routes/preferences.py` `PATCH /me/preferences`: extend the `PreferencesPatch` Pydantic model with `analytics_opted_out: bool | None = Field(default=None)`. `extra='forbid'` already covers the rejection of unknown keys. Return both columns in the canonical-state response. **Do not** add a new `PATCH /me/analytics_opt_out` route — fold the toggle into the existing preferences surface, same posture as Day 25's `weekly_digest_enabled`.
- `frontend/src/lib/preferencesApi.ts`: extend the `Preferences` interface with `analytics_opted_out: boolean`. Drop the now-stale comment about "would need to start returning preferences too" — `/me` does that now.

### 2. Frontend `analytics.ts` — typed wrapper + leak-free init

`frontend/src/lib/analytics.ts`:

- Initializes `posthog-js` with `VITE_POSTHOG_KEY` and `VITE_POSTHOG_HOST`. Config flags (all required):
  - `autocapture: false` — DOM-event leak vector; manual `track()` only.
  - `capture_pageview: false` — URL paths can carry transaction ids or chat-conversation ids; never auto-capture.
  - `disable_session_recording: true` — would record transaction amounts visually.
  - `mask_all_text: true` — defense in depth even with session recording off, in case a future config flip leaks.
  - `opt_out_capturing_by_default: true` — **the leak-free-init invariant.** PostHog starts opted out; events queued before the SDK opt-in resolve are dropped, not buffered.
- After app bootstrap calls `GET /me`, if `analytics_opted_out === false` call `posthog.opt_in_capturing()`. The brief no-events window during cold load is the correct tradeoff — at v1 scale, no events are lost that matter.
- Single export: `track(event_name, properties)`. **Hard whitelist via TypeScript discriminated union** — anything not in the union is a compile error:

  ```ts
  type OnboardingStepName =
    | 'philosophy'      // matches OnboardingStep in features/onboarding/types.ts
    | 'signin'
    | 'currency'
    | 'addCard'
    | 'csvImport'
    | 'tourCompleted'   // analytics-only milestone, not a wizard step
    | 'firstTransaction'; // post-onboarding chat milestone

  type ErrorCode =
    | 'internal_error'
    | 'import_token_expired'
    | 'offline_queue_failed'
    | 'auth_expired'
    | 'rate_limited';
    // Extend in lockstep with the central error-toast renderer.
    // A free-form string here defeats the whitelist (a developer could
    // write `code: "auth_failed user@x.com"` and leak the email).

  type Event =
    | { name: 'chat_session_started'; props: { conversation_id: string } }
    | { name: 'chat_session_ended'; props: { conversation_id: string; turn_count: number; duration_ms: number } }
    | { name: 'feature_used'; props: { feature: 'dashboard' | 'chat' | 'csv_import' | 'card_added' | 'subscription_added' } }
      // DESIGN.md §9.5 also lists 'manual_entry'; CLAUDE.md invariant 8
      // forbids a separate manual-entry form in v1, so the literal is
      // reserved for the post-Phase-1 path and is intentionally omitted
      // from the v1 union. Reintroduce only when that path actually ships.
    | { name: 'onboarding_step_completed'; props: { step: OnboardingStepName } }
    | { name: 'weekly_digest_opened'; props: Record<string, never> }
    | { name: 'error_shown'; props: { code: ErrorCode } };
  ```

- User identification: `posthog.identify(user_id)` after sign-in confirms a user. **No email, no name, no metadata.** Call `posthog.reset()` on sign-out so the next user (if any on the same device) starts with a fresh `distinct_id`.

### 3. Wire `track()` calls into pages

- **Chat:** `chat_session_started` fires from the chat store's stream `onDone` handler the first time a turn resolves with a conversation id (either a fresh server-minted one or a rehydrated id on first turn-of-page-load). `chat_session_ended` fires from `chatStore.newChat()` — the explicit "start a fresh thread" affordance — carrying the conversation id, the count of successful round-trips, and `Date.now() - sessionStartedAt`. Session metrics live in a module-scope `sessionMetrics` object inside `chatStore.ts`, not in the rendered `ChatState`; a page reload resets them (acceptable at v1 — cross-reload sessions are rare and not worth localStorage complexity). **Deferred: the "10-min idle ended" case is not captured.** Plumbing the server-side distillation-piggyback signal (memory.md 2026-05-17) into the SSE `done` payload would catch app-close sessions but adds backend churn; revisit if "users with `chat_session_started` but no matching `chat_session_ended`" turns out to be a meaningful gap in the PostHog data.
- **CSV import, card add, subscription add:** `feature_used` fires on the `/confirm` success handler in each propose-confirm flow (chat-driven per CLAUDE.md invariant 8). Dashboard `feature_used` fires on first dashboard mount per session.
- **Onboarding:** `onboarding_step_completed` fires when each step transitions away (the existing `setStep(next)` call site in `frontend/src/pages/onboarding.tsx`). Two extra firing points outside the wizard: `tourCompleted` from `onboarding.tour.tsx` when the user reaches the last screen and dismisses, `firstTransaction` from the chat propose-confirm success handler when it's the user's first `transactions` row (cheap check: `count === 0` before insert).
- **Email opens / `weekly_digest_opened`:** fired by the PWA landing handler when `?source=digest` is present. Mechanism + email CTA spec live in Day 26b.
- **Errors:** wrap the central error-toast renderer (`frontend/src/components/ErrorToast.tsx` or equivalent) — on render, if the error has a known `code` from the `ErrorCode` union, fire `error_shown`. Unknown codes are *not* tracked (vs. mapped to `internal_error`) — silent miss is safer than potentially-leaky catch-all.

### 4. Frontend Settings → Privacy: opt-out toggle

`frontend/src/pages/settings.tsx`:

- Toggle "Pause product analytics". Default reads `analytics_opted_out` from `/me`-bootstrapped state.
- On flip:
  1. `await updatePreferences({ analytics_opted_out: <new value> })` (extended Day 25 helper).
  2. If now opted out: `posthog.opt_out_capturing()` then `posthog.reset()` (rotate the anonymous id so no further events can be attributed to the prior session).
  3. If now opted in: `posthog.opt_in_capturing()`.
- Inline copy under the toggle: "Stops product-usage events immediately. Already-collected data is retained per PostHog's default until manual deletion." (Server-side `delete_person` is a §17 deliverable — DESIGN.md line 1683 — when the Privacy Policy ships. Until then, opt-out stops new events but does not wipe prior data; the inline copy must not overpromise.)
- **Disclosure prose lives in Day 27.** This prompt ships the toggle UI + inline label only.

## Don't

- Don't add any custom event not in the whitelist. New event = explicit code change to the `Event` union + DESIGN.md §9.5 update in the same commit.
- Don't widen `ErrorCode` or `OnboardingStepName` to `string` to "make it easier." A string is not a whitelist.
- Don't track question text, transaction amounts, merchant names, category names, conversation contents, or anything user-typed.
- Don't enable `autocapture`, `capture_pageview`, or session recording. The four config flags above are load-bearing.
- Don't add a server-side PostHog Python SDK. v1's only event-firing surface is the browser. Server-side opt-out checks under service role are a §17 concern.
- Don't add a `POST /webhooks/resend/opened` route or any Resend open-tracking pixel — DESIGN.md §6.4 forbids it, and Day 26b provides the clean alternative.
- Don't add a `PATCH /me/analytics_opt_out` route — extend `/me/preferences` (Day 25 pattern).
- Don't claim "data is wiped on opt-out" in any UI copy unless the server-side delete actually ships (§17 only).

## Done when

- `GET /me` returns `analytics_opted_out` and `weekly_digest_enabled` alongside `home_currency`.
- `PATCH /me/preferences` accepts `analytics_opted_out`; unknown keys still 422 (extra=forbid).
- A network-tab inspection during cold load shows **zero requests to `us.i.posthog.com` before** `/me` resolves, even for non-opted-out users. (Opt-out-by-default + opt-in-after-`/me` is the reason; this is the regression guard.)
- An opted-out user produces zero network requests to PostHog across a full chat-and-import session.
- Toggling opt-out in Settings then performing a tracked action produces no PostHog request. Toggling opt-in restores tracking.
- TypeScript build fails on `track('something_random', { amount: 47 })`, `track('error_shown', { code: 'made_up' })`, `track('feature_used', { feature: 'manual_entry' })`, and `track('onboarding_step_completed', { step: 'splash' })`.
- PostHog live-events view shows the whitelisted events from real usage on the dev machine.
- Settings → Privacy renders the toggle with the "Pause product analytics" label and the inline copy about opt-out behavior. (Verbatim disclosure copy is Day 27 scope and is **not** on this page yet.)
- Sign-out clears the PostHog `distinct_id` (`posthog.reset()` confirmed via PostHog devtools).
