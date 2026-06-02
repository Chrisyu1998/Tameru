/**
 * i18next setup for UI chrome translation (DESIGN.md §6.6 Tier 2b).
 *
 * The language is NOT owned by i18next — it's driven by the store's
 * `uiLanguage` axis (the same source `displayLocale()` and `useCategoryLabel()`
 * read), so there is a single source of language truth. `main.tsx` calls
 * `i18n.changeLanguage(...)` from the same effect that sets `<html lang>`
 * whenever `uiLanguage` changes; we deliberately do NOT use
 * `i18next-browser-languagedetector`, which would introduce a second,
 * competing language state.
 *
 * Resources are static JSON (en / ja / zh-TW), one default `translation`
 * namespace with keys nested by surface (`nav.home`, `settings.title`, …).
 * `en` is the source-of-truth copy (verbatim from the original JSX, so English
 * rendering is unchanged); `fallbackLng: 'en'` covers any key a translation
 * file is missing. Traditional Chinese only.
 */

import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "../locales/en.json";
import ja from "../locales/ja.json";
import zhTW from "../locales/zh-TW.json";
import { detectUiLanguage } from "./uiLanguage";

export const SUPPORTED_UI_LANGUAGES = ["en", "ja", "zh-TW"] as const;
export type SupportedUiLanguage = (typeof SUPPORTED_UI_LANGUAGES)[number];

// Initial language = the browser's best-guess (via the dependency-free
// `detectUiLanguage`). The store's `uiLanguage`, once hydrated from `/me`,
// overrides this through `i18n.changeLanguage` in main.tsx. `uiLanguage.ts`
// has no supabase/store imports, so there's no cycle and no env coupling.
void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    ja: { translation: ja },
    "zh-TW": { translation: zhTW },
  },
  lng: detectUiLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React already escapes.
  returnNull: false,
});

export default i18n;
