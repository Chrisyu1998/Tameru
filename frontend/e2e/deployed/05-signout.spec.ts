/**
 * Test 5 — Sign out (`DESIGN.md` §13.5).
 *
 * Drives the More page's "sign out" row → SignOutDialog → tap
 * "sign out" → assert the page lands on /onboarding (signed-out
 * users land at the wizard's signin step via the RequireOnboarded
 * gate) and that a reload preserves the signed-out state
 * (localStorage was actually cleared, not just the in-memory store).
 */

import { expect, test } from "@playwright/test";
import { signInViaPassword } from "./_helpers";

test("sign out → /onboarding → reload still signed out", async ({ page }) => {
  await signInViaPassword(page);
  await page.waitForURL(/\/$/, { timeout: 30_000 });

  await page.goto("/more");
  await page.getByRole("button", { name: /^sign out$/i }).click();

  // SignOutDialog uses `role="alertdialog"`, not `role="dialog"` —
  // it's a destructive-action confirmation, which is the
  // alertdialog ARIA pattern. `getByRole("dialog")` does NOT match.
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText(/sign out\?/i)).toBeVisible();
  await dialog.getByRole("button", { name: /^sign out$/i }).click();

  // Post-signout, supabase.auth.signOut() clears localStorage and
  // onAuthStateChange's null branch flips the store. RequireOnboarded
  // sees no jwt → routes the unauthed user to /onboarding (splash).
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 15_000 });

  // localStorage cleared check: the persisted Supabase session key is
  // gone. (Day 19's mocked tests use a forged session in the same key,
  // so this assertion also guards against a regression where signOut
  // forgets to clear the persisted slot.)
  const persistedSession = await page.evaluate(() =>
    window.localStorage.getItem("tameru-auth"),
  );
  expect(persistedSession).toBeNull();

  // Reload — the cold-start initAuth() should resolve to no-session
  // and leave us on /onboarding.
  await page.reload();
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 15_000 });
});
