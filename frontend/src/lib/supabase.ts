import { createClient } from '@supabase/supabase-js';

/*
 * Supabase JS client — client-side only piece of auth.
 *
 * persistSession + autoRefreshToken: localStorage-backed session, refresh
 * token rotated automatically. The JWT this issues is the one our backend
 * verifies via JWKS (DESIGN.md §9.1). It is NOT stored in a cookie —
 * Authorization: Bearer is the contract (CLAUDE.md invariant 1, day-7 prompt).
 *
 * detectSessionInUrl: the OAuth and magic-link flows append `#access_token=...`
 * to the redirect URL. Supabase JS reads the hash, sets the session, and
 * scrubs the URL. Without this flag the post-OAuth landing would never
 * become authenticated.
 */
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as
  | string
  | undefined;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // Hard-fail at import time rather than later in a sign-in handler — a
  // misconfigured frontend should be obvious in the dev console on first load.
  throw new Error(
    'VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY are required. See frontend/.env.example.',
  );
}

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    storageKey: 'tameru-auth',
  },
});
