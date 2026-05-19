/**
 * /subscriptions page e2e — Day 19.
 *
 * Exercises the user-visible flows added in Day 19 / 19b / 19c through
 * a real browser (Chromium) with mocked backend. Vitest covers the
 * underlying React state machines; this suite covers the page-level
 * composition: list rendering, status buckets, the resume-with-deleted-
 * card UI swap, the AF-hide filter, and the cancel-from-detail-sheet
 * flow.
 *
 * Auth is stubbed via localStorage so Supabase JS treats the app as
 * signed-in. Every backend call to localhost:8000 is intercepted by
 * `mockApi`; no FastAPI / Postgres needed.
 */

import { expect, test } from "@playwright/test";
import { fixtures, mockApi, signInStub } from "./_fixtures";

test.beforeEach(async ({ page }) => {
  await signInStub(page);
});

test("renders active, paused, cancelled buckets", async ({ page }) => {
  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "Amex Gold",
        issuer: "amex",
        network: "amex",
        program: "MR",
        last_four: "1234",
        status: "active",
      },
    ],
    subscriptions: [
      fixtures.sub({
        id: "sub-active",
        name: "Netflix",
        amount: "15.99",
        category: "Streaming",
        status: "active",
        card_id: "card-1",
      }),
      fixtures.sub({
        id: "sub-paused",
        name: "Gym",
        amount: "30.00",
        category: "Health",
        status: "paused",
        card_id: "card-1",
      }),
      fixtures.sub({
        id: "sub-cancelled",
        name: "Old News",
        amount: "12.99",
        category: "Subscriptions",
        status: "cancelled",
        card_id: "card-1",
      }),
    ],
  });
  await page.goto("/subscriptions");

  // All three appear, each in its bucket. We assert by ROW text, not by
  // section header, because the header word ("paused" / "cancelled")
  // collides with row description text on the same page.
  await expect(page.getByText("Netflix")).toBeVisible();
  await expect(page.getByText("Gym")).toBeVisible();
  await expect(page.getByText("Old News")).toBeVisible();

  // Paused-row description: "paused · no upcoming charges".
  await expect(
    page.getByText(/paused · no upcoming charges/i),
  ).toBeVisible();
});

test("AF rows are hidden from the default list (DESIGN.md §6.5)", async ({
  page,
}) => {
  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "CSR",
        issuer: "chase",
        network: "visa",
        program: "UR",
        last_four: "9999",
        status: "active",
      },
    ],
    subscriptions: [
      fixtures.sub({
        id: "sub-netflix",
        name: "Netflix",
        amount: "15.99",
        category: "Streaming",
        status: "active",
        card_id: "card-1",
      }),
      // AF-shaped — the page should not surface it.
      fixtures.sub({
        id: "sub-af",
        name: "CSR annual fee",
        amount: "550.00",
        frequency: "annual",
        category: "Subscriptions",
        status: "active",
        card_id: "card-1",
      }),
    ],
  });
  await page.goto("/subscriptions");

  await expect(page.getByText("Netflix")).toBeVisible();
  // The AF row is conceptually a card consequence, not a user
  // subscription — `/subscriptions` calls without `include_card_af`,
  // so the page-level filter strips it.
  await expect(page.getByText("CSR annual fee")).toBeHidden();
});

test("resume on a normal paused sub fires PATCH status=active", async ({
  page,
}) => {
  const patchCalls: Array<{ id: string; body: unknown }> = [];
  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "Amex Gold",
        issuer: "amex",
        network: "amex",
        program: "MR",
        last_four: "1234",
        status: "active",
      },
    ],
    subscriptions: [
      fixtures.sub({
        id: "sub-paused",
        name: "Spotify",
        amount: "9.99",
        category: "Streaming",
        status: "paused",
        card_id: "card-1",
      }),
    ],
    onPatchSubscription: (id, body) => patchCalls.push({ id, body }),
  });
  await page.goto("/subscriptions");

  await page.getByText("Spotify").click();
  // Detail sheet open → "resume" button visible (card is still active,
  // so the resume-as-ACH swap doesn't fire).
  await page
    .getByRole("button", { name: /^\s*resume\s*$/i })
    .click();

  await expect.poll(() => patchCalls.length).toBeGreaterThan(0);
  const call = patchCalls.find((c) => c.id === "sub-paused");
  expect(call).toBeDefined();
  expect(call!.body).toEqual({ status: "active" });
});

test("resume-with-deleted-card shows 'resume as bank ACH' and PATCHes card_id=null", async ({
  page,
}) => {
  const patchCalls: Array<{ id: string; body: Record<string, unknown> }> = [];
  await mockApi(page, {
    // Empty active cards list. Backend is the source of truth for
    // soft-delete; we simulate that state by not surfacing the card.
    cards: [],
    subscriptions: [
      fixtures.sub({
        id: "sub-orphan",
        name: "GhostCard Netflix",
        amount: "15.99",
        category: "Streaming",
        status: "paused",
        // Points at a card not in the cards list — the page treats
        // this as the deleted-backing-card case (DESIGN.md §8.3
        // split-cascade rule).
        card_id: "card-deleted",
      }),
    ],
    onPatchSubscription: (id, body) =>
      patchCalls.push({ id, body: body as Record<string, unknown> }),
  });
  await page.goto("/subscriptions");

  // The top-level needs-new-card banner surfaces.
  await expect(page.getByText(/needs a new card/i)).toBeVisible();

  await page.getByText("GhostCard Netflix").click();
  // The detail sheet swaps the resume button for the ACH-fallback
  // version. The bare "resume" affordance should not appear.
  await expect(
    page.getByRole("button", { name: /resume as bank ACH/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: /resume as bank ACH/i }).click();

  // The page sequences two PATCHes: card_id → null, then status →
  // active. Both should fire and target the orphan subscription.
  await expect.poll(() => patchCalls.length).toBeGreaterThanOrEqual(2);
  const reassignCall = patchCalls.find(
    (c) => c.id === "sub-orphan" && "card_id" in c.body,
  );
  const resumeCall = patchCalls.find(
    (c) =>
      c.id === "sub-orphan" &&
      c.body.status === "active" &&
      !("card_id" in c.body),
  );
  expect(reassignCall?.body).toEqual({ card_id: null });
  expect(resumeCall?.body).toEqual({ status: "active" });
});

test("cancel button fires DELETE on the subscription", async ({ page }) => {
  const deleted: string[] = [];
  await mockApi(page, {
    cards: [
      {
        id: "card-1",
        name: "Amex Gold",
        issuer: "amex",
        network: "amex",
        program: "MR",
        last_four: "1234",
        status: "active",
      },
    ],
    subscriptions: [
      fixtures.sub({
        id: "sub-killit",
        name: "Old Service",
        amount: "5.00",
        category: "Subscriptions",
        status: "active",
        card_id: "card-1",
      }),
    ],
    onDeleteSubscription: (id) => deleted.push(id),
  });
  await page.goto("/subscriptions");

  await page.getByText("Old Service").click();
  await page.getByRole("button", { name: /cancel subscription/i }).click();

  await expect.poll(() => deleted).toContain("sub-killit");
});
