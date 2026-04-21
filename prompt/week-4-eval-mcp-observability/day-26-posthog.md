# Day 26 — PostHog product analytics (structural events only)

## Goal

Track which features are used and how often. Zero financial data. Zero question text. User can opt out.

## Read first

- `DESIGN.md` §9.5 (PostHog scope — read carefully).
- `CLAUDE.md` invariant about PostHog (no question classifier).

## Deliverables

- Frontend:
  - `frontend/src/lib/analytics.ts`:
    - Initializes `posthog-js` with `VITE_POSTHOG_KEY` and `VITE_POSTHOG_HOST`.
    - Single export: `track(event_name, properties)`.
    - On every call, checks `users_meta.analytics_opted_out` (cached in Zustand) — if true, no-ops.
    - **Hard whitelist of event names and property shapes.** Reject anything else at build time via TypeScript types:
      ```ts
      type Event =
        | { name: 'chat_session_started'; props: { conversation_id: string } }
        | { name: 'chat_session_ended'; props: { conversation_id: string; turn_count: number; duration_ms: number } }
        | { name: 'feature_used'; props: { feature: 'dashboard' | 'manual_entry' | 'chat' | 'csv_import' | 'card_added' | 'subscription_added' } }
        | { name: 'onboarding_step_completed'; props: { step: string } }
        | { name: 'weekly_digest_opened'; props: {} }
        | { name: 'error_shown'; props: { code: string } };
      ```
    - User identification: `posthog.identify(user_id)` only — no email, no name.
- Wire `track()` calls into the relevant pages:
  - Chat: `chat_session_started` on first message of a conversation, `chat_session_ended` after 10 minutes idle (matches the memory-distill trigger).
  - Manual entry, CSV import, card add, subscription add: `feature_used`.
  - Onboarding: `onboarding_step_completed` per philosophy/tour/sign-in/first-card/first-transaction.
  - Email opens: tracked via Resend's open-tracking pixel + a webhook to `POST /webhooks/resend/opened` that logs `weekly_digest_opened`.
  - Errors shown to the user (offline queue failures, 401s, etc.): `error_shown` with code only.
- Backend:
  - `app/routes/me.py`: add `PATCH /me/analytics_opt_out` to flip `users_meta.analytics_opted_out`.
- Frontend Settings → Privacy: opt-out toggle. Prominent.
- Privacy disclosure copy in Settings (verbatim from DESIGN.md §9.5 user disclosure).

## Don't

- Don't add any custom event not in the whitelist. New event = explicit code change + design doc update.
- Don't track question text, transaction amounts, merchant names, or category names.
- Don't auto-capture page views with PostHog's autocapture — too easy to leak. Manual `track()` only.

## Done when

- PostHog dashboard shows the whitelisted events from real usage.
- Inspecting network requests confirms no transaction data leaves the browser to PostHog.
- Opting out stops all events immediately.
- TypeScript build fails if you try `track('something_random', {amount: 47})`.
