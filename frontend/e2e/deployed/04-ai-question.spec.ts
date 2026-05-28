/**
 * Test 4 — Ask the AI chat one question (`DESIGN.md` §13.5).
 *
 * Drives the chat agent with a read-only question and asserts the
 * stream produces a non-empty answer with a dollar figure. Read-only
 * = no `propose_*` tool — the agent should hit `get_spending_summary`
 * or `get_transactions` and reply in prose.
 *
 * Depends on Tests 1–3 having populated some history (a typed
 * transaction from Test 2 plus the CSV from Test 3), otherwise the
 * agent legitimately says "you haven't logged anything in groceries
 * last week" — which is a correct answer but a weak assertion.
 * The CSV fixture has a Sweetgreen row dated within the last few
 * weeks, so the groceries window is non-empty.
 *
 * Also asserts no console errors fire during the streaming turn —
 * §14.5 + §9.5 disclosures depend on the agent not throwing visible
 * exceptions to the UI.
 */

import { expect, test } from "@playwright/test";
import { signInViaPassword } from "./_helpers";

test("chat: ask a spending question → streamed answer contains a number", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await signInViaPassword(page);
  await page.waitForURL(/\/$/, { timeout: 30_000 });

  await page.goto("/chat");
  await expect(page).toHaveURL(/\/chat$/);

  const composer = page.getByPlaceholder("type or tap the mic");
  await composer.fill("how much did I spend in the last 30 days?");
  await page.getByRole("button", { name: "send" }).click();

  // The agent's final reply renders as an assistant MessageBubble. We
  // can't pin specific copy (depends on the user's data), but the
  // bubble eventually contains a dollar value of some shape. A
  // generous timeout — chat round-trips through Anthropic + the tool
  // loop + SSE stream completion.
  await expect(page.getByText(/\$\d+(\.\d+)?/).first()).toBeVisible({
    timeout: 90_000,
  });

  // Drop expected console noise. PostHog "anonymous_id" cookie
  // warnings, Sentry init messages, and supabase auth-token-refresh
  // chatter are benign and don't gate launch. The CSP-violation
  // entries are pre-existing Day-27 prod state (Vercel CSP doesn't
  // allowlist `fonts.googleapis.com`, so Google Fonts requests are
  // blocked and the app falls back to system serif/sans — the
  // visual result is fine, but the browser logs the violation per
  // page load). The `Failed to load resource` lines are the
  // companion 401/CSP-blocked-resource log entries. Both are pure
  // prod noise unrelated to the E2E run; allowlist by substring.
  const ignored = [
    "posthog",
    "PostHog",
    "Sentry",
    "supabase.auth",
    "ResizeObserver",
    "Service Worker",
    "Content Security Policy",
    "fonts.googleapis.com",
    "Failed to load resource",
  ];
  const unexpected = consoleErrors.filter(
    (msg) => !ignored.some((needle) => msg.includes(needle)),
  );
  expect(
    unexpected,
    `Unexpected console errors: ${unexpected.join("\n")}`,
  ).toEqual([]);
});
