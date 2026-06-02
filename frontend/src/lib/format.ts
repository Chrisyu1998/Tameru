/** Currency / amount / date formatting helpers.
 *
 * The three i18n axes are independent (DESIGN.md §6.6):
 *   - WHAT currency  → from the user's immutable `home_currency` (store,
 *     hydrated from /me — invariant 13). Drives the symbol and fraction
 *     digits (¥ has 0, $ has 2) via the `Intl` currency *code*.
 *   - HOW it's formatted (number grouping, date layout, month names) → from
 *     the display locale, which follows the UI language, NOT the currency.
 *     So a JPY user with an English browser gets English dates and ¥ amounts.
 *   - Timezone is a third axis handled server-side (the digest); the browser
 *     already renders date-only values in local time, so it isn't needed here.
 *
 * Amounts are value-safe across currencies: the UI's "cents-as-number"
 * representation is major-units ×100, a precision trick — not a claim that
 * every currency has 100 minor units.
 */

import { useAppStore } from "@/store";

/** The user's home currency, or "USD" while /me is still resolving. */
function resolveCurrency(): string {
  const c = useAppStore.getState().homeCurrency;
  return typeof c === "string" && c.length === 3 ? c : "USD";
}

/**
 * The display locale that drives number grouping and date formatting. It
 * follows the UI *language*, decoupled from currency and timezone (DESIGN.md
 * §6.6). The user's explicit `ui_language` (Tier 2) wins; until they choose
 * one — or in non-browser contexts (SSR/tests) — we fall back to the
 * browser's `navigator.language`. So the sister-in-Japan case (English UI,
 * JPY currency, Tokyo timezone) renders English dates with ¥ amounts.
 *
 * For an explicit `en` choice we keep the browser's regional English
 * (`en-GB`, `en-AU`, …) so date/number layout still matches where the user
 * is; only `ja`/`zh-TW` pin a specific CJK locale.
 */
function displayLocale(): string {
  const lang = useAppStore.getState().uiLanguage;
  if (lang === "ja") return "ja-JP";
  if (lang === "zh-TW") return "zh-TW";
  const browser =
    typeof navigator !== "undefined" && navigator.language
      ? navigator.language
      : "en-US";
  if (lang === "en") {
    return browser.toLowerCase().startsWith("en") ? browser : "en-US";
  }
  // null / undefined — no explicit choice yet; track the browser language.
  return browser;
}

/**
 * The narrow currency symbol for the home currency ("$", "¥", "NT$", "£", "€").
 * For the amount-input prefix in edit sheets — the user types a major-unit
 * number, the symbol just labels which currency it's in.
 */
export function currencySymbol(currency = resolveCurrency()): string {
  const parts = new Intl.NumberFormat(displayLocale(), {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
  }).formatToParts(0);
  return parts.find((p) => p.type === "currency")?.value ?? currency;
}

/**
 * Render a major-unit amount in the user's home currency, dropping the
 * fractional part when the value is whole ("$47", "¥1,500") but keeping the
 * currency's natural precision otherwise ("$47.50"). Used directly by callers
 * that already hold major units (e.g. subscription amount strings).
 */
export function formatCurrencyAmount(value: number, currency = resolveCurrency()): string {
  const isWhole = value % 1 === 0;
  return new Intl.NumberFormat(displayLocale(), {
    style: "currency",
    currency,
    // Whole → no fraction digits across every currency. Otherwise fall back
    // to the currency's default (2 for USD/EUR/…; JPY has 0 and is always
    // whole, so it never hits the else branch).
    ...(isWhole ? { minimumFractionDigits: 0, maximumFractionDigits: 0 } : {}),
  }).format(value);
}

/** Formats cents (major-units ×100) to the home currency, e.g. "$47.50", "¥1,500". */
export function formatMoney(cents: number, opts: { signed?: boolean } = {}): string {
  const { signed = false } = opts;
  const formatted = formatCurrencyAmount(Math.abs(cents) / 100);
  if (!signed) return formatted;
  if (cents > 0) return `+${formatted}`;
  if (cents < 0) return `−${formatted}`;
  return formatted;
}

export function formatPercent(value: number, opts: { signed?: boolean } = {}): string {
  const { signed = true } = opts;
  const rounded = Math.round(value);
  if (!signed) return `${Math.abs(rounded)}%`;
  if (rounded > 0) return `+${rounded}%`;
  if (rounded < 0) return `−${Math.abs(rounded)}%`;
  return "0%";
}

/** Short date like "Apr 24" (current year) or "Apr 24, 2024" (other year), localized. */
export function formatShortDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(displayLocale(), {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export function formatMonth(d: Date = new Date()): string {
  return d.toLocaleDateString(displayLocale(), { month: "long" });
}

/** Full date with year, e.g. "Mar 15, 2027" — localized. For AF renewal chips etc. */
export function formatFullDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(displayLocale(), {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
