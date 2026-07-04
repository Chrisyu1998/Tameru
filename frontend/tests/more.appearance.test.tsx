/**
 * Mobile More menu exposes the light/dark theme control.
 *
 * Regression guard for the change that removed the global floating
 * ThemeToggle (a `fixed top-4 right-4` button that overlapped the chat
 * page's new-chat button) and moved theme into Settings. On mobile the PWA
 * reaches preferences through the More menu's sheets, not the Settings page —
 * so the More → "appearance" row must open a sheet containing the ThemeRow
 * switch, and toggling it must flip the theme.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

// Node 25's experimental localStorage is half-mounted without a file path
// (same workaround as auth.deviceClaim.test.ts / voice.test.ts) — replace it
// with an in-memory shim before ThemeRow's useTheme() touches it.
const storage = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    getItem: (k: string) => storage.get(k) ?? null,
    setItem: (k: string, v: string) => void storage.set(k, v),
    removeItem: (k: string) => void storage.delete(k),
    clear: () => void storage.clear(),
  },
});

// Mock supabase so importing MorePage (→ @/lib/auth → @/lib/supabase) doesn't
// hit the import-time env check, and so the oauth listGrants probe resolves.
vi.mock("@/lib/supabase", () => ({
  supabase: {
    auth: { oauth: { listGrants: vi.fn().mockResolvedValue({ data: [], error: null }) } },
  },
}));

// Mock the preferences client used by other More rows (LanguageRow /
// TimezoneRow / digest toggle) so no real network is attempted. ThemeRow
// itself is localStorage-backed and hits none of this.
vi.mock("@/lib/preferencesApi", () => ({
  readPreferences: vi
    .fn()
    .mockResolvedValue({ weekly_digest_enabled: true, analytics_opted_out: false, timezone: null, ui_language: null }),
  updatePreferences: vi
    .fn()
    .mockResolvedValue({ weekly_digest_enabled: true, analytics_opted_out: false, timezone: null, ui_language: null }),
}));

import MorePage from "@/pages/more";

afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
  storage.clear();
});

describe("More menu → appearance", () => {
  it("renders an appearance row that opens a sheet whose switch toggles the theme", async () => {
    render(
      <MemoryRouter>
        <MorePage />
      </MemoryRouter>,
    );

    // The discoverable entry point on mobile.
    const row = await screen.findByRole("button", { name: /^appearance$/i });
    expect(row).toBeInTheDocument();

    await userEvent.click(row);

    // The sheet (portaled) shows the ThemeRow switch. Default theme is light,
    // so the switch offers "switch to dark mode" and is unchecked.
    const toggle = await screen.findByRole("switch", { name: /switch to dark mode/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    // Flipping it turns dark mode on (localStorage-backed, applied to <html>).
    await userEvent.click(toggle);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(
      screen.getByRole("switch", { name: /switch to light mode/i }),
    ).toHaveAttribute("aria-checked", "true");
  });
});
