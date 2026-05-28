/**
 * Test 1 — Sign-up golden path (`DESIGN.md` §13.5).
 *
 * Drives the actual fresh-user flow:
 *   sign-in (REST + localStorage shortcut — see _helpers.ts for why)
 *   → currency confirm → tour (the wizard's `useEffect` auto-redirects
 *   to /onboarding/tour the moment `homeCurrency` becomes truthy) →
 *   tap "skip the tour" → land on home.
 *
 * The wizard's AddCard step is unreachable from the natural fresh-user
 * flow — the tour-redirect fires faster than `setStep("addCard")` can
 * paint, and the only entry to AddCard is the `?step=addCard` deep
 * link (used by the tour's final CTAs only for some flows). Real-user
 * card-add happens through chat (`askToAddCard` on `/cards` seeds a
 * chat prompt and navigates to /chat → propose_card → confirm), which
 * Test 3 exercises. Test 1's done-when is "land on home as a
 * fully-onboarded user," not "with a card."
 *
 * Google-OAuth + magic-link arms are intentionally not exercised here
 * — Google's bot detection makes E2E flaky, magic-link needs a mail
 * catcher we don't run against prod, and both are covered by Vitest +
 * manual UAT. Password sign-in is the only auth path E2E touches.
 */

import { expect, test } from "@playwright/test";
import { signInViaPassword } from "./_helpers";

test("sign-up golden path lands the user on home", async ({ page }) => {
  await signInViaPassword(page);

  // RequireOnboarded routes to /onboarding because the freshly-minted
  // user has no users_meta.home_currency.
  await expect(page).toHaveURL(/\/onboarding/);

  // Currency step: confirm button reads "I understand — set <CODE>".
  // `detectDefaultCurrency()` resolves USD on the CI runner's en-US
  // default locale.
  await page
    .getByRole("button", { name: /I understand — set/i })
    .click();

  // The wizard's `useEffect` watches (jwt, homeCurrency) and navigates
  // to /onboarding/tour as soon as homeCurrency becomes a string. That
  // fires immediately after CurrencyStep's handleConfirm() calls
  // `setHomeCurrency` in the store, before the wizard's own
  // `goTo("addCard")` can paint.
  await page.waitForURL(/\/onboarding\/tour/, { timeout: 30_000 });

  // The tour's non-final screens render a "skip the tour" link below
  // the "next" button. For a fully-onboarded user (jwt + home_currency
  // both set), tapping it calls markOnboarded() and navigates to "/".
  await page.getByRole("button", { name: /skip the tour/i }).click();

  // Home — the onboarding gate is satisfied.
  await expect(page).toHaveURL(/\/$/, { timeout: 15_000 });
});
