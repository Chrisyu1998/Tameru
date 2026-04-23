import { create } from 'zustand';

/*
 * Placeholder app-wide store. Day 7 wires real values in:
 *   - user / jwt: from Supabase JS session
 *   - deviceId: generated UUID persisted to localStorage, used in the
 *     X-Device-Id header for single-active-device enforcement
 *     (DESIGN.md §9.1, CLAUDE.md invariant 5).
 *
 * Keep this file stable — api.ts reads from it via getState() to construct
 * Authorization and X-Device-Id headers on every request.
 */

export type User = {
  id: string;
  email: string;
};

type AppStore = {
  user: User | null;
  jwt: string | null;
  deviceId: string | null;
  setSession: (next: {
    user: User | null;
    jwt: string | null;
    deviceId: string | null;
  }) => void;
  clearSession: () => void;
};

export const useAppStore = create<AppStore>((set) => ({
  user: null,
  jwt: null,
  deviceId: null,
  setSession: (next) => set(next),
  clearSession: () => set({ user: null, jwt: null, deviceId: null }),
}));
