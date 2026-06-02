/**
 * Day 29 Tier 2 internationalization — frontend (DESIGN.md §6.6).
 *
 * The UI language drives two things tested here: the formatting locale
 * (`displayLocale()`, exercised via formatMonth) and the category display
 * labels. Both read `useAppStore.getState().uiLanguage`, so flipping it must
 * change their output. The stored category enum stays English regardless.
 */

import { afterEach, describe, expect, it } from "vitest";

import i18n from "@/lib/i18n";
import { resolveUiLanguage } from "@/lib/uiLanguage";
import { categoryLabel } from "@/lib/categories";
import { formatMonth } from "@/lib/format";
import { useAppStore } from "@/store";

function withBrowserLanguage(lang: string, fn: () => void) {
  const original = Object.getOwnPropertyDescriptor(navigator, "language");
  Object.defineProperty(navigator, "language", { value: lang, configurable: true });
  try {
    fn();
  } finally {
    if (original) Object.defineProperty(navigator, "language", original);
  }
}

afterEach(() => {
  useAppStore.getState().setUiLanguage(undefined);
  void i18n.changeLanguage("en");
});

describe("category display labels follow ui_language", () => {
  it("translates the rendered label while the enum key stays English", () => {
    useAppStore.getState().setUiLanguage("en");
    expect(categoryLabel("Dining")).toBe("Dining");

    useAppStore.getState().setUiLanguage("ja");
    expect(categoryLabel("Dining")).toBe("外食");

    useAppStore.getState().setUiLanguage("zh-TW");
    expect(categoryLabel("Dining")).toBe("餐飲");
  });

  it("falls back to the raw value for a non-enum input", () => {
    useAppStore.getState().setUiLanguage("ja");
    expect(categoryLabel("Mystery")).toBe("Mystery");
  });

  it("defaults to English when no language is chosen", () => {
    useAppStore.getState().setUiLanguage(null);
    expect(categoryLabel("Dining")).toBe("Dining");
  });
});

describe("resolveUiLanguage — explicit wins, unset falls back to browser", () => {
  it("returns the explicit stored choice unchanged", () => {
    // Explicit choice must win even on a mismatched browser.
    withBrowserLanguage("ja-JP", () => {
      expect(resolveUiLanguage("en")).toBe("en");
    });
    expect(resolveUiLanguage("ja")).toBe("ja");
    expect(resolveUiLanguage("zh-TW")).toBe("zh-TW");
  });

  it("falls back to the BROWSER language (not 'en') when unset", () => {
    // The regression Codex caught: null/undefined must track the browser so a
    // ja/zh-TW browser keeps localized chrome, matching displayLocale().
    withBrowserLanguage("ja-JP", () => {
      expect(resolveUiLanguage(null)).toBe("ja");
      expect(resolveUiLanguage(undefined)).toBe("ja");
    });
    withBrowserLanguage("zh-TW", () => {
      expect(resolveUiLanguage(null)).toBe("zh-TW");
    });
    withBrowserLanguage("en-GB", () => {
      expect(resolveUiLanguage(null)).toBe("en");
    });
    withBrowserLanguage("fr-FR", () => {
      // Unsupported browser language → English chrome (only 3 UI languages).
      expect(resolveUiLanguage(undefined)).toBe("en");
    });
  });
});

describe("i18next chrome translation (Tier 2b)", () => {
  it("resolves UI strings in the active language across all three locales", async () => {
    await i18n.changeLanguage("en");
    expect(i18n.t("nav.home")).toBe("home");
    expect(i18n.t("nav.settings")).toBe("settings");

    await i18n.changeLanguage("ja");
    expect(i18n.t("nav.home")).toBe("ホーム");
    expect(i18n.t("nav.settings")).toBe("設定");

    await i18n.changeLanguage("zh-TW");
    expect(i18n.t("nav.home")).toBe("首頁");
    expect(i18n.t("nav.settings")).toBe("設定");
  });

  it("falls back to English for a key missing in a translation (no raw key leaks)", async () => {
    await i18n.changeLanguage("ja");
    // Every key present in en must resolve to *some* non-key string in ja
    // (either a translation or the English fallback) — never the raw key.
    const sample = ["chat.send", "settings.title", "cards.title", "onboarding.back"];
    for (const k of sample) {
      expect(i18n.t(k)).not.toBe(k);
    }
  });
});

describe("formatting locale follows ui_language", () => {
  it("renders month names in the chosen language", () => {
    const april = new Date(2026, 3, 1);

    useAppStore.getState().setUiLanguage("en");
    expect(formatMonth(april)).toBe("April");

    useAppStore.getState().setUiLanguage("ja");
    expect(formatMonth(april)).toBe("4月");
  });
});
