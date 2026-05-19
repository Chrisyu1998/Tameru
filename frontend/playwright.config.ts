/**
 * Playwright config — Day 19 UI tests.
 *
 * Scope: page-level integration tests that exercise the full React tree
 * with mocked backend responses. The vitest suite covers component
 * logic + store behavior in isolation; this suite covers user-visible
 * flows that span store, page, components, and routing — the kind of
 * thing a unit test can pass while the actual page is broken.
 *
 * No live backend / Supabase needed: `page.route(...)` intercepts all
 * calls to localhost:8000 (the FastAPI base) and Supabase auth is
 * short-circuited via `addInitScript` seeding localStorage with a fake
 * session before the React tree mounts.
 *
 * `webServer` boots Vite on a deterministic port so CI doesn't have to
 * orchestrate a separate dev-server start.
 */

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "line",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
