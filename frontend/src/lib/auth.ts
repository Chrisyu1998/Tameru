import { supabase } from './supabase';
import { apiJson } from './api';
import { useAppStore, type UiLanguage } from '../store';
import { identifyUser, setOptOut, track } from './analytics';
import { chatStore } from './chatStore';
import { detectUiLanguage, resolveUiLanguage } from './uiLanguage';

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
  // Day 26: rides along on /me so the PostHog wrapper can resolve the
  // user's choice before lighting up. Defaults match the column defaults
  // (analytics opted in / digest opted in) for pre-bootstrap users.
  analytics_opted_out: boolean;
  weekly_digest_enabled: boolean;
  // Day 29 (DESIGN.md §6.6): per-user IANA timezone, or null when unset.
  // Decoupled from home_currency — drives the weekly digest's local send
  // time and week-boundary math. Editable in Settings.
  timezone: string | null;
  // Day 29 Tier 2 (DESIGN.md §6.6): the UI/display language, or null when
  // the user hasn't made an explicit choice. Drives displayLocale(), the
  // chat reply language (chat_v12), and the digest language.
  ui_language: UiLanguage;
};

/**
 * The browser's IANA timezone (e.g. "Asia/Tokyo"), or null if the runtime
 * can't report one. Sent at bootstrap so the digest defaults to the user's
 * actual zone rather than a currency-derived guess (DESIGN.md §6.6).
 */
export function detectTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

// The UI-language helpers live in the dependency-free `uiLanguage.ts` so they
// can be used (and unit-tested) without loading supabase via this module.
// Re-exported here so existing `@/lib/auth` importers keep working.
export { detectUiLanguage, resolveUiLanguage };

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
): Promise<{
  home_currency: AllowedCurrency;
  active_device_id: string;
  timezone: string | null;
  ui_language: UiLanguage;
}> {
  return apiJson('/auth/bootstrap', {
    method: 'POST',
    body: {
      device_id: deviceId,
      home_currency: homeCurrency,
      // Best-effort: the backend validates and stores NULL if absent/invalid.
      timezone: detectTimezone(),
      // Snapshot the browser language onto the supported set (DESIGN.md §6.6
      // Tier 2). Always one of en/ja/zh-TW; the user changes it in Settings.
      ui_language: detectUiLanguage(),
    },
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

export async function signInWithGoogle(returnTo?: string): Promise<void> {
  // Supabase redirects to Google, then back to the app's origin with the
  // session in the URL hash. detectSessionInUrl on the client picks it up
  // and fires onAuthStateChange — the post-auth dispatch lives in Splash.
  //
  // `returnTo` is opt-in for flows that must preserve their own URL state
  // across the OAuth round-trip (e.g., the OAuth consent page needs to
  // hold onto `?authorization_id=...`). Default is `${origin}/`, which
  // routes through the onboarding gate.
  await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: returnTo ?? `${window.location.origin}/` },
  });
}

export async function signInWithMagicLink(
  email: string,
  returnTo?: string,
): Promise<void> {
  // Same `returnTo` semantics as signInWithGoogle. supabase-js's
  // detectSessionInUrl consumes the auth params from the URL hash, not
  // the query string, so a `?authorization_id=...` query param on
  // `returnTo` survives the redirect untouched.
  await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: returnTo ?? `${window.location.origin}/` },
  });
}

export async function signOut(): Promise<void> {
  // The SDK opt-out + distinct_id rotation happens in store.clearSession()
  // (Day 26 P2 fix), which fires from onAuthStateChange's null branch
  // when supabase.auth.signOut() resolves. Centralizing the side effect
  // there means the device-displaced modal's defensive clearSession()
  // and any future session-ending path also get it for free.
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

    supabase.auth.onAuthStateChange((event, session) => {
      if (session) {
        useAppStore.getState().setSession({
          user: { id: session.user.id, email: session.user.email ?? '' },
          jwt: session.access_token,
          deviceId,
        });
        // Best-effort: refresh home_currency on every auth change so the
        // onboarding gate sees the latest server view (e.g. post-bootstrap
        // re-auth on a different tab). On a Supabase 'SIGNED_IN' event we
        // also fire the `signin` onboarding milestone — but only after
        // /me resolves and `setOptOut(false)` has run, otherwise the
        // SDK is still opted-out-by-default and the capture drops.
        // Skipped on TOKEN_REFRESHED / USER_UPDATED / INITIAL_SESSION
        // (those are passive). The signin path also catches re-sign-in
        // from the device-displaced modal. (Codex 2026-05-23 P2/P3.)
        void refreshHomeCurrency({ fireSigninMilestone: event === 'SIGNED_IN' });
      } else {
        // Sign-out / token-refresh-failed — end any in-flight chat
        // session for analytics (Codex 2026-05-23 P2) then clear the
        // JWT. clearSession itself opts the SDK out (store.ts) so any
        // events captured between now and the next /me are dropped.
        chatStore.endSession();
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
      // Block init on /me so the first paint of the onboarding gate /
      // home redirect already knows whether home_currency is set. If /me
      // fails we leave homeCurrency=undefined and the gate falls through
      // to a signed-in-but-unknown state — the user can still navigate.
      await refreshHomeCurrency();
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
 * Fetch /me, stash home_currency, and — if the user has already bootstrapped
 * — claim this browser as the active device.
 *
 * Called from initAuth on boot and from onAuthStateChange after any sign-in.
 * After a successful /auth/bootstrap, callers should set home_currency in the
 * store directly (we already have the value) rather than round-tripping /me.
 *
 * The claim_device call here is what makes "sign in on a second browser"
 * actually work: without it, /me succeeds (auth is JWT-only), but the next
 * authenticated request 401s with DEVICE_DISPLACED because the server's
 * active_device_id still points at the previous browser. claimDevice tells
 * the server "this browser is the new active one" — the previous browser's
 * next /auth/check_device poll will then see it's been displaced.
 *
 * Skipped when home_currency is null — that means the user hasn't completed
 * /auth/bootstrap yet, and bootstrap itself sets active_device_id in the
 * same transaction. Calling claim_device first would fail.
 */
interface RefreshHomeCurrencyOptions {
  /**
   * When true and /me confirms the user is opted in, fires the
   * `onboarding_step_completed: signin` analytics event after
   * `setOptOut(false)` and `identifyUser()` have run. Set only from
   * the `SIGNED_IN` Supabase event path — `INITIAL_SESSION` and
   * `TOKEN_REFRESHED` are passive and don't qualify. Required because
   * the SDK is opted-out-by-default at boot and after every
   * `clearSession()`, so firing the milestone any earlier than this
   * point silently drops the event (Codex 2026-05-23 P2).
   */
  fireSigninMilestone?: boolean;
}

export async function refreshHomeCurrency(
  options: RefreshHomeCurrencyOptions = {},
): Promise<void> {
  // Snapshot identity at fetch start so a sign-out (or sign-in as a
  // different user) that races the in-flight /me can't apply stale
  // side effects on resolution. Without this guard, an opt-out flip
  // by clearSession() can be undone by a late /me from the prior
  // session, re-enabling PostHog and re-identifying the previous user
  // (Codex 2026-05-23 P2).
  const startingJwt = useAppStore.getState().jwt;
  const startingUserId = useAppStore.getState().user?.id ?? null;
  try {
    const me = await fetchMe();
    const current = useAppStore.getState();
    // Bail out if the session changed under us. Three races to
    // catch: (a) signed-out mid-flight (jwt now null); (b) signed in
    // as a different user (user.id differs from snapshot); (c) /me
    // returned data for someone else (defense in depth — backend
    // shouldn't, but a misrouted response shouldn't move analytics).
    if (
      current.jwt !== startingJwt ||
      current.user?.id !== startingUserId ||
      current.user?.id !== me.user_id
    ) {
      return;
    }
    useAppStore.getState().setHomeCurrency(me.home_currency);
    // Day 29 Tier 2: mirror the UI language from /me so displayLocale() and
    // the category-label helper resolve the right locale on first paint.
    useAppStore.getState().setUiLanguage(me.ui_language);
    // Day 26: mirror analytics opt-out from /me into the store and flip
    // the PostHog SDK to match. This is the leak-free-init invariant's
    // commit point — before this resolves, the SDK is opted out via
    // opt_out_capturing_by_default. identifyUser ties subsequent events
    // to user_id; no email/name/PII attached (DESIGN.md §9.5).
    useAppStore.getState().setAnalyticsOptedOut(me.analytics_opted_out);
    setOptOut(me.analytics_opted_out);
    if (!me.analytics_opted_out) {
      identifyUser(me.user_id);
      // Fire the signin milestone *after* opt-in has actually taken
      // effect, otherwise the SDK is still opted out from the init
      // default and the capture drops on the floor.
      if (options.fireSigninMilestone) {
        track('onboarding_step_completed', { step: 'signin' });
      }
    }
    if (me.home_currency !== null) {
      const { deviceId } = useAppStore.getState();
      if (deviceId) {
        try {
          await claimDevice(deviceId);
          // Successful claim invalidates any latched displacement state
          // that was left over from a previous session.
          useAppStore.getState().setDisplaced(false);
        } catch {
          // claim_device 4xx isn't fatal — if it failed because the user
          // genuinely is displaced (e.g. they're signed in elsewhere too),
          // the next API call will surface it via the 401 latch and the
          // user can re-sign-in from there.
        }
      }
    }
  } catch {
    // /me failure: leave whatever's in the store. The displaced modal
    // handles the 401 DEVICE_DISPLACED path on its own.
  }
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
