/**
 * Mobile More menu exposes the UI-language picker (DESIGN.md §6.6 Tier 2).
 *
 * Regression guard for the gap a user hit: the language selector lived only in
 * Settings → Account, which the mobile PWA's More menu never links to — so
 * there was no in-app path to change language on mobile. The More → "language"
 * row must open a sheet containing the LanguageRow selector (English / 日本語 /
 * 繁體中文).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

// Mock supabase so importing MorePage (→ @/lib/auth → @/lib/supabase) doesn't
// hit the import-time env check, and so the oauth listGrants probe resolves.
vi.mock("@/lib/supabase", () => ({
  supabase: {
    auth: { oauth: { listGrants: vi.fn().mockResolvedValue({ data: [], error: null }) } },
  },
}));

// Mock the preferences client used by LanguageRow + TimezoneRow + the digest
// toggle so no real network is attempted.
vi.mock("@/lib/preferencesApi", () => ({
  readPreferences: vi
    .fn()
    .mockResolvedValue({ weekly_digest_enabled: true, analytics_opted_out: false, timezone: null, ui_language: null }),
  updatePreferences: vi
    .fn()
    .mockResolvedValue({ weekly_digest_enabled: true, analytics_opted_out: false, timezone: null, ui_language: "ja" }),
}));

import MorePage from "@/pages/more";

afterEach(() => cleanup());

describe("More menu → language", () => {
  it("renders a language row that opens a sheet with the language selector", async () => {
    render(
      <MemoryRouter>
        <MorePage />
      </MemoryRouter>,
    );

    // The discoverable entry point on mobile.
    const row = await screen.findByRole("button", { name: /^language$/i });
    expect(row).toBeInTheDocument();

    await userEvent.click(row);

    // The sheet (portaled) shows the LanguageRow selector with all three
    // languages, each labelled in its own script.
    expect(await screen.findByRole("option", { name: "English" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "日本語" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "繁體中文" })).toBeInTheDocument();
  });
});
