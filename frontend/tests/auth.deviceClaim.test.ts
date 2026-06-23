/**
 * Audit P2-2 — claim_device fires only on explicit sign-in.
 *
 * The single-active-device invariant degenerated into a two-device
 * ping-pong because refreshHomeCurrency claimed the device slot on EVERY
 * session-bearing auth event: a displaced device re-took active status on
 * its next silent token refresh (~every 4-5 min at the 300s JWT TTL), the
 * other device then 401'd and re-took it back, forever.
 *
 * Pins the Option-A contract:
 *   - boot hydration (persisted session) does NOT claim
 *   - TOKEN_REFRESHED / INITIAL_SESSION do NOT claim
 *   - SIGNED_IN DOES claim
 *   - SIGNED_IN while the displacement latch is set does NOT claim
 *     (only the modal's explicit buttons exit that state)
 */

import { beforeEach, describe, expect, test, vi } from 'vitest';

const apiCalls: string[] = [];
let authCallback:
  | ((event: string, session: Record<string, unknown> | null) => void)
  | null = null;

const SESSION = {
  user: { id: 'user-1', email: 'a@tameru.test' },
  access_token: 'jwt-1',
};

vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      onAuthStateChange: (
        cb: (event: string, session: Record<string, unknown> | null) => void,
      ) => {
        authCallback = cb;
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      },
      getSession: async () => ({ data: { session: SESSION } }),
      signOut: vi.fn(),
    },
  },
}));

vi.mock('@/lib/api', () => ({
  apiJson: vi.fn(async (path: string) => {
    apiCalls.push(path);
    if (path === '/me') {
      return {
        user_id: 'user-1',
        email: 'a@tameru.test',
        home_currency: 'USD',
        analytics_opted_out: true,
        weekly_digest_enabled: true,
        timezone: null,
        ui_language: 'en',
      };
    }
    if (path === '/auth/claim_device') {
      return { active_device_id: 'device-x' };
    }
    return {};
  }),
}));

vi.mock('@/lib/analytics', () => ({
  identifyUser: vi.fn(),
  setOptOut: vi.fn(),
  track: vi.fn(),
}));

vi.mock('@/lib/chatStore', () => ({
  chatStore: { endSession: vi.fn() },
}));

import { initAuth } from '@/lib/auth';
import { useAppStore } from '@/store';

// Node 25's experimental localStorage is half-mounted without a file path
// (same workaround as voice.test.ts) — replace it with an in-memory shim
// before getOrCreateDeviceId touches it.
const storage = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k: string) => storage.get(k) ?? null,
    setItem: (k: string, v: string) => void storage.set(k, v),
    removeItem: (k: string) => void storage.delete(k),
    clear: () => void storage.clear(),
  },
});

function claimCount(): number {
  return apiCalls.filter((p) => p === '/auth/claim_device').length;
}

function meCount(): number {
  return apiCalls.filter((p) => p === '/me').length;
}

async function fire(event: string): Promise<void> {
  expect(authCallback).not.toBeNull();
  authCallback!(event, SESSION);
  // refreshHomeCurrency is fire-and-forget from the auth callback; give
  // its /me + claim chain a few microtask turns to settle.
  await vi.waitFor(() => {
    expect(meCount()).toBeGreaterThanOrEqual(1);
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('device-claim gating (audit P2-2, Option A)', () => {
  beforeEach(() => {
    useAppStore.getState().setDisplaced(false);
  });

  test('boot + passive events never claim; SIGNED_IN claims; latched SIGNED_IN does not', async () => {
    await initAuth();
    // Boot hydration ran /me but must not claim — a page reload is not
    // user intent to take over the device slot.
    expect(meCount()).toBeGreaterThanOrEqual(1);
    expect(claimCount()).toBe(0);

    await fire('INITIAL_SESSION');
    expect(claimCount()).toBe(0);

    await fire('TOKEN_REFRESHED');
    expect(claimCount()).toBe(0);

    await fire('SIGNED_IN');
    await vi.waitFor(() => {
      expect(claimCount()).toBe(1);
    });

    // While the displacement latch is set, even SIGNED_IN must not
    // claim — only the modal's explicit buttons exit that state.
    useAppStore.getState().setDisplaced(true);
    await fire('SIGNED_IN');
    expect(claimCount()).toBe(1);
  });
});
