/**
 * CSV import e2e — Day 20.
 *
 * Walks the user-visible flow from Settings → Import → done. Both
 * `/imports/csv/preview` and `/imports/csv/commit` are mocked at the
 * network layer so this test doesn't need a live FastAPI / Postgres /
 * Gemini. The SSE response for /commit is pre-formed as a single byte
 * payload — the frontend's stream reader parses multi-frame chunks
 * identically to a real chunked response, so this exercises the
 * actual SSE parser without needing a streaming mock primitive.
 *
 * Coverage:
 *   1. happy path: pick file → confirm columns → see done summary,
 *      with the ledger refresh firing afterward.
 *   2. low-confidence path: manual column picker renders + accepts
 *      the user's mapping including the sign_convention toggle.
 *   3. error path: a tampered import_token surfaces 422 as a visible
 *      error step.
 */

import { expect, test } from "@playwright/test";
import { mockApi, signInStub } from "./_fixtures";

const API_BASE = "http://localhost:8000";

// Use a mobile viewport so only the mobile settings UI renders. The
// page renders both mobile (md:hidden) and desktop (hidden md:flex)
// trees in the DOM, which trips strict-mode locators at desktop sizes.
// PWA mobile is the v1 target anyway (CLAUDE.md invariant 7).
test.use({ viewport: { width: 414, height: 800 } });

// A small CSV file path uploaded via setInputFiles. Body content is
// irrelevant — the mock for /preview returns a hardcoded column
// mapping regardless of what we upload.
const SAMPLE_CSV = [
  "Transaction Date,Description,Amount",
  "04/12/2026,BLUE BOTTLE COFFEE,5.50",
  "04/13/2026,WHOLE FOODS MARKET,84.32",
  "04/14/2026,CHEVRON,42.10",
  "",
].join("\n");

test.beforeEach(async ({ page }) => {
  await signInStub(page);
});

test("happy path: pick file → confirm columns → done summary", async ({
  page,
}) => {
  let previewCalls = 0;
  let commitCalls = 0;
  let ledgerRefreshes = 0;

  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "Amex Platinum",
        issuer: "amex",
        network: "amex",
        program: "MR",
        last_four: "1007",
        status: "active",
      },
    ],
  });

  // Count GET /transactions calls so we can assert the post-done
  // ledger refresh actually fires. mockApi pre-registers a default
  // handler for this; we override with a counter version.
  await page.route(
    new RegExp(`^${API_BASE}/transactions(\\?.*)?$`),
    async (route) => {
      ledgerRefreshes += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], has_more: false }),
      });
    },
  );

  await page.route(`${API_BASE}/imports/csv/preview`, async (route) => {
    previewCalls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        detected_columns: {
          date: "Transaction Date",
          merchant: "Description",
          amount: "Amount",
          currency: null,
          sign_convention: "charges_positive",
          confidence: 0.95,
        },
        sample_rows: [
          {
            "Transaction Date": "04/12/2026",
            Description: "BLUE BOTTLE COFFEE",
            Amount: "5.50",
          },
        ],
        confidence: 0.95,
        import_token: "e2e-token",
        total_rows: 3,
      }),
    });
  });

  await page.route(`${API_BASE}/imports/csv/commit`, async (route) => {
    commitCalls += 1;
    // Pre-built SSE payload. The frontend's stream reader parses
    // multi-frame chunks fine, so atomically returning the whole
    // payload still exercises the real parser.
    const frames = [
      'event: progress\ndata: {"processed":1,"total":3,"current_category":"Coffee Shops"}\n\n',
      'event: progress\ndata: {"processed":2,"total":3,"current_category":"Groceries"}\n\n',
      'event: progress\ndata: {"processed":3,"total":3,"current_category":"Gas"}\n\n',
      'event: done\ndata: {"done":true,"inserted":3,"skipped_duplicates":0,"skipped_refunds":0,"skipped_foreign_currency":0,"skipped_parse_errors":0}\n\n',
    ].join("");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: frames,
    });
  });

  await page.goto("/settings");

  // Open the import panel. Mobile and desktop both render a clickable
  // "import" entry; tap it. (md+ shows it as a sidebar nav item.)
  // Tap "import" in the mobile settings list → renders the panel.
  // Mobile and desktop variants of the settings tree both render in
  // DOM; scope every interactive locator to the visible subtree.
  await page
    .getByRole("button", { name: /^import$/i })
    .locator(":visible")
    .first()
    .click();
  await page.locator('[data-testid="open-import-csv"]:visible').click();

  // Pick the file (hidden input).
  const fileInput = page.getByTestId("csv-file-input");
  await fileInput.setInputFiles({
    name: "sample.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(SAMPLE_CSV, "utf-8"),
  });

  // Card defaults to the first card (Amex Platinum). Click "next".
  await page.getByRole("button", { name: /^next$/i }).click();

  // Preview landed → "looks right" button visible.
  const confirmBtn = page.getByRole("button", { name: /looks right/i });
  await expect(confirmBtn).toBeVisible();
  await expect(page.getByText(/3 rows/i)).toBeVisible();
  await confirmBtn.click();

  // Done summary lands. The "all set" copy + the count are the user-
  // visible success signal.
  await expect(page.getByText(/all set/i)).toBeVisible();
  await expect(page.getByText(/3 transactions imported/i)).toBeVisible();

  // Network shape: exactly one preview, one commit, ledger refresh
  // fired at least once (initial mount + post-done; we don't care
  // about the exact pre-done count, only that >=2).
  expect(previewCalls).toBe(1);
  expect(commitCalls).toBe(1);
  expect(ledgerRefreshes).toBeGreaterThanOrEqual(2);
});

test("low-confidence path renders the manual mapping picker", async ({
  page,
}) => {
  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "Generic Card",
        issuer: "other",
        network: "other",
        program: "Other",
        last_four: "9999",
        status: "active",
      },
    ],
  });

  await page.route(`${API_BASE}/imports/csv/preview`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        needs_manual_mapping: true,
        headers: ["field_a", "field_b", "field_c"],
        sample_rows: [{ field_a: "2026-04-01", field_b: "X", field_c: "1.00" }],
        import_token: "e2e-token-manual",
        total_rows: 1,
      }),
    });
  });

  let commitMapping: unknown = null;
  await page.route(`${API_BASE}/imports/csv/commit`, async (route) => {
    const form = await route.request().postData();
    if (form) {
      // FormData multipart parsing is fiddly; pull the
      // column_mapping field by regex. This is testing-only code.
      const match = /name="column_mapping"\r?\n\r?\n([^\r\n]+)/.exec(form);
      if (match) {
        commitMapping = JSON.parse(match[1]);
      }
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: done\ndata: {"done":true,"inserted":1,"skipped_duplicates":0,"skipped_refunds":0,"skipped_foreign_currency":0,"skipped_parse_errors":0}\n\n',
    });
  });

  await page.goto("/settings");
  // Mobile and desktop variants of the settings tree both render in
  // DOM; scope every interactive locator to the visible subtree.
  await page
    .getByRole("button", { name: /^import$/i })
    .locator(":visible")
    .first()
    .click();
  await page.locator('[data-testid="open-import-csv"]:visible').click();
  await page.getByTestId("csv-file-input").setInputFiles({
    name: "weird.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("field_a,field_b,field_c\n2026-04-01,X,1.00\n", "utf-8"),
  });
  await page.getByRole("button", { name: /^next$/i }).click();

  // Manual-mapping form lands.
  await expect(page.getByText(/map columns/i)).toBeVisible();
  // Flip the charges-negative toggle so we can verify it round-trips.
  await page.getByTestId("manual-mapping-negative-charges").check();
  // Scope to the sheet so we don't match the settings list's "import"
  // tab button by the same name.
  const sheet = page.getByRole("dialog", { name: /import csv/i });
  await sheet.getByRole("button", { name: /^import$/i }).click();
  await expect(page.getByText(/all set/i)).toBeVisible();

  expect(commitMapping).toMatchObject({
    date: "field_a",
    merchant: "field_b",
    amount: "field_c",
    sign_convention: "charges_negative",
  });
});

test("422 from /preview surfaces as a visible error step", async ({
  page,
}) => {
  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "Test Card",
        issuer: "chase",
        network: "visa",
        program: "UR",
        last_four: "1234",
        status: "active",
      },
    ],
  });

  await page.route(`${API_BASE}/imports/csv/preview`, async (route) => {
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: "invalid_card",
          message: "card_id does not resolve to one of your cards",
        },
      }),
    });
  });

  await page.goto("/settings");
  // Mobile and desktop variants of the settings tree both render in
  // DOM; scope every interactive locator to the visible subtree.
  await page
    .getByRole("button", { name: /^import$/i })
    .locator(":visible")
    .first()
    .click();
  await page.locator('[data-testid="open-import-csv"]:visible').click();
  await page.getByTestId("csv-file-input").setInputFiles({
    name: "x.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("a,b,c\n1,2,3\n", "utf-8"),
  });
  await page.getByRole("button", { name: /^next$/i }).click();

  await expect(page.getByText(/import couldn't finish/i)).toBeVisible();
  await expect(page.getByText(/card_id does not resolve/i)).toBeVisible();
  // The structured code badge surfaces too.
  await expect(page.getByText(/invalid_card/i)).toBeVisible();
});
