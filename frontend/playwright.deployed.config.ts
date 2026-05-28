/**
 * Playwright config — Day 28 deployed E2E suite.
 *
 * Distinct from the default `playwright.config.ts` (which boots a Vite
 * dev server and runs Day 19's mocked-backend tests against
 * 127.0.0.1:5173). This config points at the deployed PWA on Vercel +
 * the live FastAPI on Railway + the prod Supabase project. No webServer
 * is started — the deploy itself is the prerequisite.
 *
 * Sign-in routes through `auth.signInWithPassword` driven from
 * `page.evaluate` (memory.md 2026-05-20 "Browser-driving the Tameru
 * PWA"). Google OAuth and magic-link are intentionally out of E2E —
 * Google's bot detection / Mailpit absence make them flaky and they're
 * covered by Vitest + manual UAT instead.
 *
 * Required env vars at run time:
 *   E2E_BASE_URL          — deployed frontend, e.g. https://tameru-seven.vercel.app
 *   E2E_TEST_EMAIL        — produced by `scripts/e2e_user.py create`
 *   E2E_TEST_PASSWORD     — same
 *   VITE_SUPABASE_URL     — prod Supabase URL (reused by the in-page client)
 *   VITE_SUPABASE_ANON_KEY — prod anon key (reused by the in-page client)
 *
 * Run locally:
 *   E2E_BASE_URL=https://tameru-seven.vercel.app \
 *   E2E_TEST_EMAIL=... E2E_TEST_PASSWORD=... \
 *   npm run e2e:deployed
 *
 * Day 28 launch-gate suite — keep it to the five §13.5 flows. Adding a
 * sixth test means amending §13.5 first.
 */

import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL;
if (!baseURL) {
  throw new Error(
    "E2E_BASE_URL is required. Set it to the deployed frontend URL " +
      "(e.g. https://tameru-seven.vercel.app).",
  );
}

export default defineConfig({
  testDir: "./e2e/deployed",
  // Run tests serially. The Day 28 suite shares one E2E user, and tests
  // mutate that user's ledger — running in parallel would race the
  // onboarding-then-log-then-import sequence.
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  // CI retries cover transient Anthropic/Gemini/Supabase blips; locally
  // we want failures to surface fast.
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "line",
  // Real prod calls — chat streaming + Anthropic latency means a single
  // turn can take ~10s; CSV preview + Gemini extraction adds more.
  timeout: 90_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // Tighten the per-action timeout below the test timeout so a
    // missing selector fails the right thing.
    actionTimeout: 20_000,
    // Match a typical iPhone-13 viewport — Tameru's primary surface is
    // a mobile PWA, and several layouts use `md:hidden` to swap mobile
    // vs desktop variants of the same component.
    viewport: { width: 390, height: 844 },
  },
  projects: [
    {
      name: "chromium-mobile",
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } },
    },
  ],
});
