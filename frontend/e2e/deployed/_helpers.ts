/**
 * Shared helpers for the Day 28 deployed E2E suite.
 *
 * Three surfaces:
 *
 *   1. `getCreds()` — reads the per-run credentials minted by
 *      `scripts/e2e_user.py create` from the process env. Fails fast
 *      with a clear message if they're missing.
 *
 *   2. `signInViaPassword(page)` — drives the Supabase REST
 *      `/auth/v1/token?grant_type=password` endpoint directly from
 *      `page.evaluate`, then stamps the resulting session into
 *      `localStorage['tameru-auth']` in the shape supabase-js v2 stores
 *      (the bare Session object — see `frontend/src/lib/supabase.ts`'s
 *      `storageKey: 'tameru-auth'` config and the Day 19 mocked-test
 *      fixture for the persisted shape). A final navigation to `/`
 *      triggers `initAuth()`, whose `supabase.auth.getSession()` reads
 *      the persisted session and fans out to the Zustand store +
 *      RequireOnboarded gate.
 *
 *      Why this and not `signInWithPassword` on an in-page client:
 *      memory.md 2026-05-20 ("Browser-driving the Tameru PWA") relied
 *      on `await import('/src/lib/supabase.ts')`, which only works
 *      against the Vite dev server. Vite-built prod bundles use hashed
 *      module ids; no stable import path is available. The REST call
 *      + localStorage stamp is bundle-agnostic and doesn't require
 *      exposing the supabase client on `window`.
 *
 *   3. `waitForHome(page)` — small convenience for tests that rely on
 *      an earlier test having advanced the user past onboarding. The
 *      suite runs `fullyParallel: false` so this is safe.
 */

import { expect, type Page } from "@playwright/test";

export interface E2ECreds {
  email: string;
  password: string;
}

export function getCreds(): E2ECreds {
  const email = process.env.E2E_TEST_EMAIL;
  const password = process.env.E2E_TEST_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "E2E_TEST_EMAIL / E2E_TEST_PASSWORD are not set. Run " +
        "`python scripts/e2e_user.py create` first, then export the " +
        "three KEY=VALUE lines it prints.",
    );
  }
  return { email, password };
}

function getSupabaseConfig(): { url: string; anonKey: string } {
  // Reused from the frontend env so the deployed suite hits the same
  // Supabase project the deployed PWA does. CI sets these from the
  // same secrets that feed Vercel.
  const url = process.env.VITE_SUPABASE_URL;
  const anonKey = process.env.VITE_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are not set in the " +
        "Playwright process env. The deployed suite calls the Supabase " +
        "REST auth endpoint directly to mint a session.",
    );
  }
  return { url: url.replace(/\/$/, ""), anonKey };
}

/**
 * Sign the page in as the E2E user. After this returns, the React tree
 * has hydrated with the user's JWT and the router has landed wherever
 * `initAuth()` + the onboarding gate decides — typically /onboarding
 * (currency step) for a freshly-minted user with no users_meta row,
 * or / for one that's been bootstrapped.
 *
 * Callers should assert their expected URL after this returns; this
 * helper deliberately doesn't, so it works for both new-user and
 * returning-user shapes.
 */
export async function signInViaPassword(
  page: Page,
  creds: E2ECreds = getCreds(),
): Promise<void> {
  const config = getSupabaseConfig();

  // Hit the REST endpoint from the page so the resulting session is
  // same-origin-readable (cookies aren't in play; localStorage is). We
  // could fetch from the Playwright worker process and then inject,
  // but doing it page-side keeps a single source of truth for the
  // session payload (whatever Supabase returns is what we persist).
  await page.goto("/signin");
  await page.waitForLoadState("networkidle");

  const sessionJson = await page.evaluate(
    async ({ url, anonKey, email, password }) => {
      const resp = await fetch(
        `${url}/auth/v1/token?grant_type=password`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            apikey: anonKey,
          },
          body: JSON.stringify({ email, password }),
        },
      );
      const body = await resp.json();
      if (!resp.ok) {
        return {
          ok: false as const,
          status: resp.status,
          body,
        };
      }
      // supabase-js v2 persists the bare Session shape under the
      // configured storageKey (see Day 19 _fixtures.ts for the exact
      // shape). `expires_at` is unix seconds; the REST endpoint
      // returns it.
      window.localStorage.setItem("tameru-auth", JSON.stringify(body));
      return { ok: true as const };
    },
    { url: config.url, anonKey: config.anonKey, ...creds },
  );

  if (!sessionJson.ok) {
    throw new Error(
      `Supabase password sign-in failed (HTTP ${sessionJson.status}): ` +
        JSON.stringify(sessionJson.body),
    );
  }

  // Navigate to "/" so `initAuth()` runs, reads the just-persisted
  // session via `supabase.auth.getSession()`, and routes the user
  // through the onboarding gate. A reload is the simplest way to
  // force a fresh module init in case the page is already mounted.
  await page.goto("/");
  await expect(page).not.toHaveURL(/\/signin$/);
}

/**
 * Wait for the post-onboarding home page to settle. Useful for specs
 * that rely on a prior spec having driven onboarding to completion.
 */
export async function waitForHome(page: Page): Promise<void> {
  await page.waitForURL(
    (url) => url.pathname === "/" || url.pathname === "/home",
    { timeout: 30_000 },
  );
}
