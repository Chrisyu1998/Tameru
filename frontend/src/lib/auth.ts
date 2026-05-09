import { supabase } from './supabase';
import { apiJson } from './api';
import { useAppStore } from '../store';

/*
 * Day 7 — single-active-device + home-currency capture.
 *
 * Three responsibilities:
 *   - manage the per-browser `device_id` (UUID in localStorage, generated once)
 *   - sync Supabase JS auth state into our zustand store so api.ts can attach
 *     the Authorization header without taking a Supabase dependency
 *   - expose the small set of /auth/* and /me wrappers the UI dispatches on
 *
 * The Supabase session is the source of truth for "are we signed in"; the
 * store mirrors it. The mirror exists because api.ts has to read jwt+deviceId
 * synchronously while building each request — pulling from Supabase JS at
 * request time would be either async or stale.
 */

const DEVICE_ID_KEY = 'tameru-device-id';

export const ALLOWED_CURRENCIES = [
  'USD',
  'EUR',
  'GBP',
  'CAD',
  'AUD',
  'JPY',
  'CHF',
  'SGD',
  'TWD',
] as const;

export type AllowedCurrency = (typeof ALLOWED_CURRENCIES)[number];

export type MeResponse = {
  user_id: string;
  email: string;
  home_currency: AllowedCurrency | null;
};

export type CheckDeviceResponse = {
  is_active: boolean;
  active_device_id: string | null;
  active_since: string | null;
};

export function getOrCreateDeviceId(): string {
  let id = localStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    // crypto.randomUUID is available in all PWA-capable browsers; the PWA
    // is HTTPS-only in prod and localhost in dev, both of which expose it.
    id = crypto.randomUUID();
    localStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

export async function fetchMe(): Promise<MeResponse> {
  return apiJson<MeResponse>('/me');
}

export async function bootstrap(
  deviceId: string,
  homeCurrency: AllowedCurrency,
): Promise<{ home_currency: AllowedCurrency; active_device_id: string }> {
  return apiJson('/auth/bootstrap', {
    method: 'POST',
    body: { device_id: deviceId, home_currency: homeCurrency },
  });
}

export async function claimDevice(
  deviceId: string,
): Promise<{ active_device_id: string }> {
  return apiJson('/auth/claim_device', {
    method: 'POST',
    body: { device_id: deviceId },
  });
}

export async function checkDevice(
  deviceId: string,
): Promise<CheckDeviceResponse> {
  return apiJson(
    `/auth/check_device?device_id=${encodeURIComponent(deviceId)}`,
  );
}

export async function signInWithGoogle(): Promise<void> {
  // Supabase redirects to Google, then back to the app's origin with the
  // session in the URL hash. detectSessionInUrl on the client picks it up
  // and fires onAuthStateChange — the post-auth dispatch lives in Splash.
  await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: `${window.location.origin}/` },
  });
}

export async function signInWithMagicLink(email: string): Promise<void> {
  await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${window.location.origin}/` },
  });
}

export async function signOut(): Promise<void> {
  await supabase.auth.signOut();
}

let _initPromise: Promise<void> | null = null;

/**
 * Initialize auth on app boot. Idempotent.
 *
 * 1. Subscribe to `onAuthStateChange` so subsequent sign-ins, refreshes, and
 *    sign-outs flow into the store.
 * 2. Hydrate from any existing persisted session before resolving so the
 *    initial route render sees the right `jwt` value.
 *
 * Returns a promise that resolves once the initial hydration is done.
 */
export function initAuth(): Promise<void> {
  if (_initPromise) return _initPromise;
  _initPromise = (async () => {
    const deviceId = getOrCreateDeviceId();

    supabase.auth.onAuthStateChange((_event, session) => {
      if (session) {
        useAppStore.getState().setSession({
          user: { id: session.user.id, email: session.user.email ?? '' },
          jwt: session.access_token,
          deviceId,
        });
      } else {
        // Sign-out / token-refresh-failed — clear the JWT but keep deviceId
        // around so we don't churn it on a re-sign-in.
        useAppStore.getState().clearSession();
      }
    });

    const { data } = await supabase.auth.getSession();
    if (data.session) {
      useAppStore.getState().setSession({
        user: {
          id: data.session.user.id,
          email: data.session.user.email ?? '',
        },
        jwt: data.session.access_token,
        deviceId,
      });
    } else {
      // No persisted session — preseed deviceId so api.ts has it ready.
      useAppStore.getState().setSession({
        user: null,
        jwt: null,
        deviceId,
      });
    }
  })();
  return _initPromise;
}

/**
 * Pick a sensible default currency from the browser locale, falling back
 * to USD. The user always sees and confirms — this is just a starting
 * value, not a silent decision (Day 7 prompt: "speed-tap through it
 * choosing USD must be a deliberate choice, not a surprise").
 */
export function detectDefaultCurrency(): AllowedCurrency {
  try {
    const locale = navigator.language || 'en-US';
    const region = new Intl.Locale(locale).maximize().region;
    // ISO-3166 alpha-2 region → home currency. Eurozone members all map
    // to EUR. Anything outside the allowed set falls through to USD.
    const map: Record<string, AllowedCurrency> = {
      US: 'USD',
      GB: 'GBP',
      CA: 'CAD',
      AU: 'AUD',
      JP: 'JPY',
      CH: 'CHF',
      SG: 'SGD',
      TW: 'TWD',
      DE: 'EUR',
      FR: 'EUR',
      IT: 'EUR',
      ES: 'EUR',
      NL: 'EUR',
      PT: 'EUR',
      BE: 'EUR',
      AT: 'EUR',
      IE: 'EUR',
      FI: 'EUR',
      GR: 'EUR',
      LU: 'EUR',
      EE: 'EUR',
      LV: 'EUR',
      LT: 'EUR',
      SK: 'EUR',
      SI: 'EUR',
      MT: 'EUR',
      CY: 'EUR',
      HR: 'EUR',
    };
    if (region && map[region]) return map[region];
  } catch {
    // Intl.Locale missing or malformed locale — fall through.
  }
  return 'USD';
}

/**
 * 60-second poll of /auth/check_device. Day 7 prompt requirement — catches
 * displacement when the user is idle and otherwise wouldn't make a request.
 * Active interaction is already covered by the per-request device gate
 * inside apiFetch, so this only matters for the dashboard-staring case.
 *
 * Returns the interval handle so callers can clear it on unmount.
 */
export function startDeviceCheckPoll(): number {
  const tick = async () => {
    const { jwt, deviceId, displaced } = useAppStore.getState();
    if (!jwt || !deviceId || displaced) return;
    try {
      const res = await checkDevice(deviceId);
      // Only flag displacement when the server reports *a different*
      // active device. `is_active=false` with `active_device_id=null`
      // means the user hasn't bootstrapped yet (e.g. they're sitting
      // on /confirm-currency for >60s) — that's not displacement, it's
      // the pre-bootstrap state. Treating it as displacement would pop
      // the modal during onboarding.
      if (!res.is_active && res.active_device_id !== null) {
        useAppStore.getState().setDisplaced(true);
      }
    } catch {
      // Transient errors are fine to swallow — the next API call the
      // user makes will surface anything important via the existing
      // 401 DEVICE_DISPLACED path.
    }
  };
  return window.setInterval(tick, 60_000);
}
