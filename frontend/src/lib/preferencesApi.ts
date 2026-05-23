/*
 * Client for /me/preferences — the user-toggleable columns on users_meta
 * (currently just weekly_digest_enabled per Day 25, DESIGN.md §6.4).
 *
 * Read path: the toggle's initial value comes from /me (which would need
 * to start returning preferences too) — for v1 we just default-on in the
 * UI and write to the server on first toggle, then trust the server's
 * returned canonical state. A cleaner solution would extend /me; deferred
 * to keep Day 25's surface tight.
 */

import { apiJson } from './api';

export interface Preferences {
  weekly_digest_enabled: boolean;
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
  // Cheaper than adding a GET endpoint server-side for one boolean at v1.
  return apiJson<Preferences>('/me/preferences', {
    method: 'PATCH',
    body: {},
  });
}
