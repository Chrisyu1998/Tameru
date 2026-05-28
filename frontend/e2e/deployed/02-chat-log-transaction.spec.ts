/**
 * Test 2 — Log a transaction via chat (`DESIGN.md` §13.5).
 *
 * Drives the chat propose-then-confirm flow end-to-end:
 *   type "spent $X at <merchant>" → wait for the parse card → tap
 *   "looks right" → assert the new transaction appears in the
 *   /breakdown list.
 *
 * Relies on Test 1 having driven the user through onboarding so
 * users_meta.home_currency is set and the RequireOnboarded gate
 * routes signed-in users to "/" instead of /onboarding. The deployed
 * config runs specs serially in filename order so this ordering is
 * stable.
 *
 * No card in the wallet at this point — Test 3 adds the card. The
 * agent legitimately produces a parse card with `card_id: null`,
 * which is a supported shape (DESIGN.md §8.2).
 */

import { expect, test } from "@playwright/test";
import { signInViaPassword } from "./_helpers";

test("chat: type a transaction → tap looks right → row appears in breakdown", async ({
  page,
}) => {
  await signInViaPassword(page);
  await page.waitForURL(/\/$/, { timeout: 30_000 });

  await page.goto("/chat");
  await expect(page).toHaveURL(/\/chat$/);

  const composer = page.getByPlaceholder("type or tap the mic");
  await composer.fill("spent $47 at Trader Joe's");
  await page.getByRole("button", { name: "send" }).click();

  // The agent streams the response and then renders a ParseCard with
  // the proposal. We assert by the "looks right" CTA, which only the
  // uncommitted fresh-state parse card surfaces (committed cards turn
  // into "logged" attribution chrome).
  //
  // Don't pre-assert the amount/merchant text — the user's typed
  // message bubble also contains "$47" + "Trader Joe's", so a
  // page-wide getByText hits a strict-mode violation. The "looks
  // right" button's appearance is sufficient evidence that the parse
  // card rendered with extracted fields (the agent would have asked
  // a clarifying question instead of producing a proposal otherwise).
  const confirmBtn = page.getByRole("button", { name: /looks right/i });
  // Generous timeout — Anthropic + Gemini + Supabase round-trip plus
  // SSE stream completion can stretch on a cold prod start.
  await expect(confirmBtn).toBeVisible({ timeout: 60_000 });
  await confirmBtn.click();

  // After confirm, the ParseCard transitions from the action-button
  // state to the "logged." badge state (ParseCard.tsx:159-163), which
  // only renders once POST /transactions/confirm round-trips
  // successfully and the store flips `committed=true`. This is the
  // canonical "the row exists server-side" signal; without it, the
  // next assertion races the in-flight POST.
  await expect(page.getByText(/^logged\.$/i)).toBeVisible({
    timeout: 30_000,
  });

  // /breakdown shows aggregates only (no per-merchant text on the
  // index — that's at /breakdown/<category>). Assert the count
  // climbed above zero. The copy is always plural ("1 transactions"),
  // so we match exactly to avoid both the "0 transactions" empty
  // state and "11 transactions" / "21 transactions" substring hits.
  await page.goto("/breakdown");
  await expect(
    page.getByText("1 transactions", { exact: true }),
  ).toBeVisible({ timeout: 20_000 });
});
