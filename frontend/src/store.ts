import { create } from 'zustand';

import { setOptOut } from './lib/analytics';

/*
 * App-wide store. Source of truth for what api.ts attaches to each request:
 *   - jwt: from the Supabase JS session (initAuth + onAuthStateChange in
 *     lib/auth.ts mirror it here so request building stays synchronous)
 *   - deviceId: persistent UUID in localStorage, used in X-Device-Id for
 *     single-active-device enforcement (DESIGN.md §9.1, CLAUDE.md invariant 5)
 *   - displaced: latched on a 401 DEVICE_DISPLACED from any API call OR on
 *     a failed /auth/check_device poll. Renders the displacement modal
 *     globally; the only exit is signing in again, which clears it.
 *   - homeCurrency: hydrated from /me on auth bootstrap.
 *       undefined = not yet fetched (still booting / no session)
 *       null = signed in but hasn't completed /auth/bootstrap yet → onboarding
 *       string = fully onboarded
 *     The onboarding wizard + home gate branch on these three states.
 */

export type User = {
  id: string;
  email: string;
};

export type HomeCurrency = string | null | undefined;

/**
 * Day 26: opt-out is mirrored here so analytics.track() can decide
 * synchronously. `undefined` means "not yet resolved from /me" — the
 * PostHog SDK stays opted out until this flips to a concrete boolean.
 * The leak-free-init invariant in analytics.ts depends on this tristate.
 */
export type AnalyticsOptOut = boolean | undefined;

type AppStore = {
  user: User | null;
  jwt: string | null;
  deviceId: string | null;
  displaced: boolean;
  homeCurrency: HomeCurrency;
  analyticsOptedOut: AnalyticsOptOut;
  setSession: (next: {
    user: User | null;
    jwt: string | null;
    deviceId: string | null;
  }) => void;
  clearSession: () => void;
  setDisplaced: (next: boolean) => void;
  setHomeCurrency: (next: HomeCurrency) => void;
  setAnalyticsOptedOut: (next: AnalyticsOptOut) => void;
};

export const useAppStore = create<AppStore>((set) => ({
  user: null,
  jwt: null,
  deviceId: null,
  displaced: false,
  homeCurrency: undefined,
  analyticsOptedOut: undefined,
  setSession: (next) => set(next),
  // clearSession keeps deviceId — it's a per-browser identifier, not a
  // session secret, and re-using it across sign-ins lets the user reclaim
  // their previous "this is browser A" identity if they sign in again.
  // homeCurrency and analyticsOptedOut both go back to undefined so a
  // re-sign-in re-fetches /me and PostHog's leak-free-init invariant
  // (opted out until the next /me resolves) still holds.
  //
  // Day 26: also flip the PostHog SDK opted-out here. Without this, an
  // opted-in user who signs out (or whose Supabase session expires)
  // leaves the SDK opted in — subsequent public surfaces (onboarding,
  // tour) would still fire events under that anonymous distinct id
  // until the next /me resolves and re-confirms the preference. Done
  // inside the setter so every clearSession caller (auth state change,
  // device-displaced modal, anything future) gets it for free —
  // there's no "remember to also opt out" footgun. setOptOut() is a
  // no-op when posthog-js isn't initialized (no project key), so
  // tests and key-less dev builds aren't affected.
  clearSession: () => {
    setOptOut(true);
    set((s) => ({
      user: null,
      jwt: null,
      deviceId: s.deviceId,
      homeCurrency: undefined,
      analyticsOptedOut: undefined,
    }));
  },
  setDisplaced: (next) => set({ displaced: next }),
  setHomeCurrency: (next) => set({ homeCurrency: next }),
  setAnalyticsOptedOut: (next) => set({ analyticsOptedOut: next }),
}));
