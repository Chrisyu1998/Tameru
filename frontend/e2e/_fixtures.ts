/**
 * Shared e2e helpers — Day 19.
 *
 * Two responsibilities:
 *   1. Seed a signed-in session via `addInitScript` so the React tree
 *      sees a JWT before the auth bootstrap reaches a real network.
 *      Supabase JS reads the `tameru-auth` localStorage key on init
 *      and skips the network refresh if the stored token's `expires_at`
 *      is in the future. We forge a long-expiring fake token.
 *   2. Mock backend endpoints via `page.route` so the test doesn't
 *      need a live FastAPI or Postgres. Each test composes its own
 *      `mockApi(page, { ... })` overrides on top of sensible defaults.
 */

import type { Page } from "@playwright/test";

const API_BASE = "http://localhost:8000";
const SUPABASE_BASE = "http://127.0.0.1:54321";

/** Pre-seed localStorage so Supabase JS treats the user as signed in.
 *
 * Supabase JS v2 stores the session JSON directly under the configured
 * `storageKey` (`tameru-auth` in our supabase.ts). The shape mirrors a
 * real `Session` (access_token, refresh_token, expires_at, user, …).
 * We forge a far-future `expires_at` so the auto-refresh loop doesn't
 * fire mid-test. */
export async function signInStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const now = Math.floor(Date.now() / 1000);
    const session = {
      access_token: "test.jwt.token",
      refresh_token: "test.refresh.token",
      expires_at: now + 60 * 60 * 24 * 30, // +30d
      expires_in: 60 * 60,
      token_type: "bearer",
      provider_token: null,
      provider_refresh_token: null,
      user: {
        id: "00000000-0000-0000-0000-000000000aaa",
        email: "e2e@tameru.test",
        aud: "authenticated",
        role: "authenticated",
        app_metadata: { provider: "email", providers: ["email"] },
        user_metadata: {},
        identities: [],
        created_at: new Date().toISOString(),
      },
    };
    window.localStorage.setItem("tameru-auth", JSON.stringify(session));
    // Also pre-claim a device id so the post-auth bootstrap doesn't
    // route through the device-claim screen. The store reads this from
    // localStorage on hydrate (see `frontend/src/store.ts`).
    window.localStorage.setItem(
      "tameru-device-id",
      "e2e-device-aaaaaaaa",
    );
  });
}

export interface SubscriptionFixture {
  id: string;
  card_id: string | null;
  name: string;
  amount: string;
  frequency: "monthly" | "annual" | "quarterly" | "weekly";
  start_date: string;
  next_billing_date: string;
  category: string;
  status: "active" | "paused" | "cancelled";
  client_request_id: string | null;
}

export interface CardFixture {
  id: string;
  name: string;
  issuer: string;
  network: string;
  program: string;
  last_four: string;
  status: "active" | "deleted";
}

export interface MockApiOptions {
  subscriptions?: SubscriptionFixture[];
  cards?: CardFixture[];
  /** Capture every PATCH /subscriptions/:id payload for assertions. */
  onPatchSubscription?: (id: string, body: unknown) => void;
  /** Override the PATCH response (e.g., return 422 for the resume guard). */
  patchSubscriptionResponse?: (
    id: string,
    body: Record<string, unknown>,
  ) => { status: number; body: unknown };
  /** Capture every DELETE /subscriptions/:id for assertions. */
  onDeleteSubscription?: (id: string) => void;
}

function _sub(overrides: Partial<SubscriptionFixture> = {}): SubscriptionFixture {
  return {
    id: "sub-default",
    card_id: null,
    name: "Default",
    amount: "9.99",
    frequency: "monthly",
    start_date: "2026-05-19",
    next_billing_date: "2026-06-19",
    category: "Subscriptions",
    status: "active",
    client_request_id: "00000000-0000-0000-0000-0000000019cc",
    ...overrides,
  };
}

export const fixtures = { sub: _sub };

/**
 * Install route mocks on the page. Defaults cover the read-only
 * surfaces hit on `/subscriptions` page mount (`/me`, `/cards`,
 * `/subscriptions`, `/transactions`, `/goals`, `/dashboard/*`,
 * `/memory`); tests override `subscriptions` / `cards` and supply
 * write-side handlers via the option callbacks.
 */
export async function mockApi(page: Page, opts: MockApiOptions = {}): Promise<void> {
  const subs = opts.subscriptions ?? [];
  const cards = opts.cards ?? [];

  // Reject anything pointed at the Supabase auth endpoint — `signInStub`
  // pre-seeded the session, so the app shouldn't be reaching out. If it
  // does (token refresh, OAuth callback, …), surface a 200 with the
  // stored shape so the bootstrap doesn't bail.
  await page.route(`${SUPABASE_BASE}/**`, async (route) => {
    if (route.request().url().includes("/auth/v1/user")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "00000000-0000-0000-0000-000000000aaa",
          email: "e2e@tameru.test",
          aud: "authenticated",
          role: "authenticated",
        }),
      });
      return;
    }
    // Pass-through for anything else (the test stack shouldn't reach
    // Supabase otherwise; if it does, fail with a clear 500).
    await route.fulfill({ status: 500, body: "unmocked-supabase-call" });
  });

  await page.route(`${API_BASE}/me`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "00000000-0000-0000-0000-000000000aaa",
        email: "e2e@tameru.test",
        home_currency: "USD",
      }),
    });
  });

  // The cards page (and `useLedger`) fetches both with and without
  // `?include_inactive`. Return all cards on `?include_inactive=true`,
  // active-only on the bare path. Tests pre-populate `cards` with
  // whatever they need.
  await page.route(new RegExp(`^${API_BASE}/cards(\\?.*)?$`), async (route) => {
    const url = new URL(route.request().url());
    const includeInactive = url.searchParams.get("include_inactive") === "true";
    const items = includeInactive ? cards : cards.filter((c) => c.status === "active");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items }),
    });
  });

  // /subscriptions handlers. The list endpoint honors `status` +
  // `include_card_af` query params so tests can exercise the AF-hide
  // filter. PATCH and DELETE delegate to the option callbacks for
  // per-test behavior; defaults flip the in-memory fixture and 200/204
  // back.
  await page.route(
    new RegExp(`^${API_BASE}/subscriptions(\\?.*)?$`),
    async (route) => {
      const url = new URL(route.request().url());
      const status = url.searchParams.get("status") ?? "active";
      const includeCardAf =
        url.searchParams.get("include_card_af") === "true";
      let items = subs;
      if (status !== "all") {
        items = items.filter((s) => s.status === status);
      }
      if (!includeCardAf) {
        items = items.filter(
          (s) =>
            !(
              s.name.endsWith(" annual fee") &&
              s.category === "Subscriptions" &&
              s.frequency === "annual"
            ),
        );
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items }),
      });
    },
  );

  await page.route(
    new RegExp(`^${API_BASE}/subscriptions/[^/?]+$`),
    async (route) => {
      const method = route.request().method();
      const url = new URL(route.request().url());
      const id = url.pathname.split("/").pop()!;
      if (method === "PATCH") {
        const body = (route.request().postDataJSON() ?? {}) as Record<
          string,
          unknown
        >;
        opts.onPatchSubscription?.(id, body);
        if (opts.patchSubscriptionResponse) {
          const r = opts.patchSubscriptionResponse(id, body);
          await route.fulfill({
            status: r.status,
            contentType: "application/json",
            body: JSON.stringify(r.body),
          });
          return;
        }
        // Default: merge the patch into the fixture and return 200.
        const idx = subs.findIndex((s) => s.id === id);
        if (idx === -1) {
          await route.fulfill({ status: 404, body: "not found" });
          return;
        }
        const next = { ...subs[idx], ...body };
        subs[idx] = next as SubscriptionFixture;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(next),
        });
        return;
      }
      if (method === "DELETE") {
        opts.onDeleteSubscription?.(id);
        const idx = subs.findIndex((s) => s.id === id);
        if (idx >= 0) {
          subs[idx] = { ...subs[idx], status: "cancelled" };
        }
        await route.fulfill({ status: 204, body: "" });
        return;
      }
      await route.fulfill({ status: 405, body: "method not allowed" });
    },
  );

  // Catch-all defaults for the other surfaces the layout touches. The
  // /subscriptions page in particular pulls in transactions via the
  // ledger bootstrap; we serve empty lists to keep tests focused.
  for (const [pattern, body] of [
    [`^${API_BASE}/transactions(\\?.*)?$`, { items: [], has_more: false }],
    [`^${API_BASE}/goals(\\?.*)?$`, { items: [] }],
    [`^${API_BASE}/dashboard/summary(\\?.*)?$`, {}],
    [`^${API_BASE}/memory(\\?.*)?$`, { items: [] }],
  ] as const) {
    await page.route(new RegExp(pattern), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });
  }
}
