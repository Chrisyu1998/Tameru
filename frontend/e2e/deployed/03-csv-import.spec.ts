/**
 * Test 3 — CSV import (`DESIGN.md` §13.5).
 *
 * Drives the ImportCsvSheet end-to-end against the live import
 * endpoints (POST /imports/csv/preview → POST /imports/csv/commit).
 *
 * CSV import requires a card in the wallet — `pickedCardId` is
 * required on both /preview and /commit. The wizard's AddCard step
 * is unreachable from the fresh-user flow (see Test 1's comment),
 * so Test 3 adds the card via chat first: propose_card → fill
 * last_four → "looks right." Then opens the import sheet from
 * /more, picks the just-added card, uploads the fixture, and
 * commits.
 *
 * Fixture is the existing `tests/fixtures/csv/amex_sample.csv` — 10
 * unique rows, no refunds, no foreign currency, all parseable. The
 * done-counter assertion checks "10 transactions imported."
 */

import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { signInViaPassword } from "./_helpers";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURE_PATH = path.resolve(
  __dirname,
  "../../../tests/fixtures/csv/amex_sample.csv",
);

test("csv import: add card via chat → pick file → preview → confirm → done counter", async ({
  page,
}) => {
  await signInViaPassword(page);
  await page.waitForURL(/\/$/, { timeout: 30_000 });

  // --- Step 1: add the Amex Gold card via chat ---
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/chat$/);

  await page
    .getByPlaceholder("type or tap the mic")
    .fill("add my Amex Gold card");
  await page.getByRole("button", { name: "send" }).click();

  // The agent calls propose_card, which fires lookup_card
  // (claude-haiku-4-5 + web_search_20250305). Generous timeout for
  // the round-trip — cold prod start can take 30s+.
  const cardConfirm = page.getByRole("button", { name: /looks right/i });
  await expect(cardConfirm).toBeVisible({ timeout: 90_000 });

  // CardParseCard surfaces the last_four input inline with a "1234"
  // placeholder. The confirm button is disabled until lastFourValid.
  await page.getByPlaceholder("1234").fill("1001");
  await expect(cardConfirm).toBeEnabled();
  await cardConfirm.click();

  // Wait for the card commit to materialize on the parse card before
  // the import sheet reads the ledger. CardParseCard's committed
  // state shows "added." (CardParseCard.tsx:329-330).
  await expect(page.getByText(/^added\.$/i)).toBeVisible({
    timeout: 30_000,
  });

  // --- Step 2: open the import sheet from /more ---
  await page.goto("/more");
  await page.getByRole("button", { name: /^import data$/i }).click();

  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();

  // The file input is hidden; set files directly on it. The
  // `data-testid="csv-file-input"` attr is wired in
  // ImportCsvSheet.tsx so this stays stable across visual refactors.
  await page
    .locator('input[data-testid="csv-file-input"]')
    .setInputFiles(FIXTURE_PATH);

  // Pick the Amex Gold card row. The card list renders the card's
  // `name` field as the visible label.
  await sheet.getByText(/Amex Gold/).click();

  // Advance to preview phase.
  await sheet.getByRole("button", { name: /^next$/i }).click();

  // Preview: Gemini-driven `detect_columns` returns within a few
  // seconds; the confirm-step button is literally "looks right"
  // (ImportCsvSheet.tsx ConfirmStep — same wording as the chat parse card,
  // intentional UX consistency). Scoped to the sheet so it doesn't
  // collide with the chat card-add card if its DOM is still around.
  // 90s (matching the card-lookup wait above): detect_columns is a real
  // Gemini round-trip against prod and can be slow during a degraded
  // window — a tighter 60s budget caused a false-alarm timeout here.
  const importConfirmBtn = sheet.getByRole("button", {
    name: /^looks right$/i,
  });
  await expect(importConfirmBtn).toBeVisible({ timeout: 90_000 });
  await importConfirmBtn.click();

  // Commit phase streams per-row progress. The "all set." copy from
  // DoneStep is the stable completion signal (ImportCsvSheet.tsx
  // around line 634).
  await expect(sheet.getByText(/all set\./i)).toBeVisible({
    timeout: 90_000,
  });

  // 10 rows, all unique, no refunds → all 10 land.
  await expect(
    sheet.getByText(/10\s+transactions imported/i),
  ).toBeVisible();

  await sheet.getByRole("button", { name: /^done$/i }).click();
});
