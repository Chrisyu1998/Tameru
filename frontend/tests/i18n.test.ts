/**
 * Day 29 Tier 2 internationalization — frontend (DESIGN.md §6.6).
 *
 * The UI language drives two things tested here: the formatting locale
 * (`displayLocale()`, exercised via formatMonth) and the category display
 * labels. Both read `useAppStore.getState().uiLanguage`, so flipping it must
 * change their output. The stored category enum stays English regardless.
 */

import { afterEach, describe, expect, it } from "vitest";

import { categoryLabel } from "@/lib/categories";
import { formatMonth } from "@/lib/format";
import { useAppStore } from "@/store";

afterEach(() => {
  useAppStore.getState().setUiLanguage(undefined);
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

describe("formatting locale follows ui_language", () => {
  it("renders month names in the chosen language", () => {
    const april = new Date(2026, 3, 1);

    useAppStore.getState().setUiLanguage("en");
    expect(formatMonth(april)).toBe("April");

    useAppStore.getState().setUiLanguage("ja");
    expect(formatMonth(april)).toBe("4月");
  });
});
