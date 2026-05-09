import { create } from 'zustand';

/*
 * App-wide store. Source of truth for what api.ts attaches to each request:
 *   - jwt: from the Supabase JS session (initAuth + onAuthStateChange in
 *     lib/auth.ts mirror it here so request building stays synchronous)
 *   - deviceId: persistent UUID in localStorage, used in X-Device-Id for
 *     single-active-device enforcement (DESIGN.md §9.1, CLAUDE.md invariant 5)
 *   - displaced: latched on a 401 DEVICE_DISPLACED from any API call OR on
 *     a failed /auth/check_device poll. Renders the displacement modal
 *     globally; the only exit is signing in again, which clears it.
 */

export type User = {
  id: string;
  email: string;
};

type AppStore = {
  user: User | null;
  jwt: string | null;
  deviceId: string | null;
  displaced: boolean;
  setSession: (next: {
    user: User | null;
    jwt: string | null;
    deviceId: string | null;
  }) => void;
  clearSession: () => void;
  setDisplaced: (next: boolean) => void;
};

export const useAppStore = create<AppStore>((set) => ({
  user: null,
  jwt: null,
  deviceId: null,
  displaced: false,
  setSession: (next) => set(next),
  // clearSession keeps deviceId — it's a per-browser identifier, not a
  // session secret, and re-using it across sign-ins lets the user reclaim
  // their previous "this is browser A" identity if they sign in again.
  clearSession: () =>
    set((s) => ({ user: null, jwt: null, deviceId: s.deviceId })),
  setDisplaced: (next) => set({ displaced: next }),
}));
