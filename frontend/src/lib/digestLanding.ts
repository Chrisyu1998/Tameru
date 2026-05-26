/**
 * Day 26b — fire `weekly_digest_opened` when the PWA loads with
 * `?source=digest` (set by the Monday-morning digest email's CTA).
 *
 * Mechanism: read URLSearchParams once on boot; if `source === 'digest'`,
 * call `track('weekly_digest_opened', {})` and strip the param via
 * `history.replaceState`. The strip is the real defense against re-firing
 * (URLSearchParams won't match on the next render); the module-level
 * `fired` flag closes StrictMode's double-invoke window of `useEffect`.
 *
 * Call site contract: must run AFTER `initAuth()` resolves, because that
 * chain (`initAuth → refreshHomeCurrency → setOptOut(false)`) is what
 * flips the PostHog SDK out of opt-out-by-default. Running earlier means
 * `track()` no-ops for opted-in users on a cold landing.
 *
 * Anonymous-device landings are an accepted measurement gap (see Day 26b
 * §2): without a session, `setOptOut(false)` never runs, the SDK stays
 * opted-out, and `track()` is a no-op. At v1 scale (invariant 5 — single
 * active device per user) most digest taps come from the device that
 * already holds the session.
 */

import { track } from './analytics';

let fired = false;

/**
 * Fire `weekly_digest_opened` if the current URL carries `?source=digest`,
 * then strip the param. Idempotent — second and later calls do nothing.
 */
export function initDigestLandingTracking(): void {
  if (fired) return;
  if (typeof window === 'undefined') return; // SSR / vitest jsdom-less guard
  const params = new URLSearchParams(window.location.search);
  if (params.get('source') !== 'digest') return;
  fired = true;
  track('weekly_digest_opened', {});
  params.delete('source');
  const rest = params.toString();
  const nextUrl =
    window.location.pathname +
    (rest ? `?${rest}` : '') +
    window.location.hash;
  window.history.replaceState({}, '', nextUrl);
}

/**
 * Test-only seam — vitest resets the module-level `fired` flag between
 * cases. Not imported by app code.
 */
export const _testing = {
  reset(): void {
    fired = false;
  },
};
