/**
 * PostHog product-analytics wrapper (Day 26, DESIGN.md §9.5).
 *
 * Doctrine:
 *   - Hard whitelist via the `Event` discriminated union below. A
 *     compile error is the regression guard against ad-hoc events.
 *   - Leak-free init: PostHog ships opted-out via
 *     `opt_out_capturing_by_default: true`. The SDK only opts in once
 *     `/me` confirms `analytics_opted_out === false`. Cold-load events
 *     queued before that resolve are dropped, not buffered.
 *   - No financial data, no question text, no merchant names, no
 *     category names. The TypeScript types enforce shape; this comment
 *     enforces intent.
 *   - No autocapture, no automatic pageview, no session recording.
 *     Manual track() only.
 *
 * Region: US Cloud (`VITE_POSTHOG_HOST=https://us.i.posthog.com`). EU
 * Cloud + GDPR consent flow is a §17 scaling-plan deliverable.
 */

import posthog from 'posthog-js';
import type { OnboardingStep } from '@/features/onboarding/types';

/**
 * Onboarding milestones tracked by `onboarding_step_completed`. Re-uses
 * the existing wizard literal where possible (splash + csvProcessing are
 * transient and intentionally omitted), plus two analytics-only
 * milestones that fire from outside the wizard:
 *   - `tourCompleted`     — the user reached the end of /onboarding/tour
 *   - `firstTransaction`  — the user's first confirmed transaction
 */
export type OnboardingStepName =
  | Exclude<OnboardingStep, 'splash' | 'csvProcessing'>
  | 'tourCompleted'
  | 'firstTransaction';

/**
 * Closed set of user-visible error codes. Free-form strings would defeat
 * the whitelist (a developer could leak an email by sticking it in the
 * code). Extend in lockstep with the surfaces that fire `error_shown` —
 * see voice.ts for the `voice_*` family, lib/api.ts for the
 * `auth_expired` / `internal_error` family.
 */
export type ErrorCode =
  | 'internal_error'
  | 'auth_expired'
  | 'rate_limited'
  | 'offline_queue_failed'
  | 'import_token_expired'
  // Voice (browser SpeechRecognition error codes — bounded set).
  | 'voice_no_speech'
  | 'voice_not_allowed'
  | 'voice_network'
  | 'voice_audio_capture'
  | 'voice_unknown'
  | 'voice_unsupported';

/**
 * The hard whitelist of analytics events. Anything outside this union
 * is a compile error at every track() call site.
 */
export type Event =
  | { name: 'chat_session_started'; props: { conversation_id: string } }
  | {
      name: 'chat_session_ended';
      props: {
        conversation_id: string;
        turn_count: number;
        duration_ms: number;
      };
    }
  | {
      name: 'feature_used';
      props: {
        // DESIGN.md §9.5 also lists 'manual_entry'; CLAUDE.md invariant 8
        // forbids a separate manual-entry form in v1, so the literal is
        // reserved for the post-Phase-1 path and is intentionally
        // omitted from the v1 union.
        // `data_export` fires from the "Export my data" button on the
        // /privacy and Settings → Privacy surfaces (Day 27, §9.6) — a
        // structural usage signal only; no payload size or filename.
        feature:
          | 'dashboard'
          | 'chat'
          | 'csv_import'
          | 'card_added'
          | 'subscription_added'
          | 'data_export';
      };
    }
  | { name: 'onboarding_step_completed'; props: { step: OnboardingStepName } }
  | { name: 'weekly_digest_opened'; props: Record<string, never> }
  | { name: 'error_shown'; props: { code: ErrorCode } };

type EventName = Event['name'];
type PropsOf<N extends EventName> = Extract<Event, { name: N }>['props'];

const POSTHOG_KEY = import.meta.env.VITE_POSTHOG_KEY as string | undefined;
const POSTHOG_HOST =
  (import.meta.env.VITE_POSTHOG_HOST as string | undefined) ??
  'https://us.i.posthog.com';

// `enabled` reflects whether the SDK was actually initialized. When the
// project key is blank (local dev without a PostHog org, CI, tests), the
// wrapper becomes a true no-op — no init, no requests, no console noise.
let enabled = false;
let initialized = false;

/**
 * Initialize PostHog if a project key is configured. Idempotent. Safe
 * to call before /me resolves — the SDK starts opted out and stays that
 * way until `setOptOut(false)` flips it.
 *
 * Boot-time dispatchers should call this once, near root mount, *before*
 * dispatching the first /me fetch.
 */
export function initAnalytics(): void {
  if (initialized) return;
  initialized = true;
  if (!POSTHOG_KEY) return; // no key → fully disabled (dev/CI default)
  posthog.init(POSTHOG_KEY, {
    api_host: POSTHOG_HOST,
    // The four flags are load-bearing for the privacy posture.
    autocapture: false,
    capture_pageview: false,
    disable_session_recording: true,
    mask_all_text: true,
    // Leak-free init: SDK starts opted out. opt_in_capturing() flips
    // this after /me confirms the user's preference.
    opt_out_capturing_by_default: true,
    // Stop posthog-js from probing for the autocapture toolbar in dev.
    advanced_disable_decide: true,
  });
  enabled = true;
}

/**
 * Reflect the server-confirmed opt-out into the SDK. Called from
 * lib/auth.ts after /me resolves, and from Settings when the user
 * toggles. `true` ⇒ opt out (and reset distinct_id so prior events
 * can't be retroactively attributed); `false` ⇒ opt in.
 */
export function setOptOut(optedOut: boolean): void {
  if (!enabled) return;
  if (optedOut) {
    posthog.opt_out_capturing();
    // Rotate the anonymous id so further events on this device can't be
    // tied to the now-private session.
    posthog.reset();
  } else {
    posthog.opt_in_capturing();
  }
}

/**
 * Attach an authenticated user id to subsequent events. No email, no
 * name, no metadata — DESIGN.md §9.5.
 */
export function identifyUser(userId: string): void {
  if (!enabled) return;
  posthog.identify(userId);
}

/**
 * Drop the current PostHog identity (called on sign-out). The next
 * sign-in produces a fresh anonymous id which `identifyUser()` can then
 * rebind.
 */
export function resetIdentity(): void {
  if (!enabled) return;
  posthog.reset();
}

/**
 * Fire one whitelisted event. The discriminated-union signature is the
 * structural privacy guard — the compiler rejects any name/props pair
 * not in `Event`. When PostHog is disabled (no key) or the user is
 * opted out, this is a no-op.
 */
export function track<N extends EventName>(name: N, props: PropsOf<N>): void {
  if (!enabled) return;
  // posthog-js gracefully no-ops capture() when opted out, so we don't
  // re-check the flag here — the SDK is the source of truth.
  posthog.capture(name, props as Record<string, unknown>);
}

/**
 * Test-only seam: expose the disabled-by-default path for vitest. Not
 * imported by app code; vitest mocks `posthog-js` and toggles `enabled`
 * via this helper to exercise both branches.
 */
export const _testing = {
  reset(): void {
    enabled = false;
    initialized = false;
  },
  forceEnabled(value: boolean): void {
    enabled = value;
    initialized = true;
  },
};
