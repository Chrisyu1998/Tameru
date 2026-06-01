/*
 * Client for /me/preferences — the user-toggleable columns on users_meta.
 *
 * v1 columns:
 *   - weekly_digest_enabled  (Day 25, DESIGN.md §6.4)
 *   - analytics_opted_out    (Day 26, DESIGN.md §9.5)
 *   - timezone               (Day 29, DESIGN.md §6.6 — IANA zone, mutable)
 *
 * The initial value of both columns now rides on /me (see lib/auth.ts
 * MeResponse), so first paint already knows the user's preference and
 * no double round-trip is required. PATCH still returns the canonical
 * post-write state so callers can drop their optimistic value.
 */

import { apiJson } from './api';

export interface Preferences {
  weekly_digest_enabled: boolean;
  analytics_opted_out: boolean;
  timezone: string | null;
}

export type PreferencesPatch = Partial<Preferences>;

export async function updatePreferences(patch: PreferencesPatch): Promise<Preferences> {
  return apiJson<Preferences>('/me/preferences', {
    method: 'PATCH',
    body: patch,
  });
}

export async function readPreferences(): Promise<Preferences> {
  // PATCH with empty body is a no-op write that returns canonical state.
  // Kept for callers that need to re-read after a side-effect (e.g. the
  // /unsubscribe route flipping weekly_digest_enabled out of band).
  return apiJson<Preferences>('/me/preferences', {
    method: 'PATCH',
    body: {},
  });
}
