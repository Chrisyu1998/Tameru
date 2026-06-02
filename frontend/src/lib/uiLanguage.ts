/**
 * Pure UI-language helpers (DESIGN.md §6.6 Tier 2) — browser-locale detection
 * and effective-language resolution. Deliberately ZERO imports so anything can
 * use them without dragging in supabase/auth (importing `auth.ts` throws at
 * load when `VITE_SUPABASE_*` is unset, e.g. in CI/test). `auth.ts` re-exports
 * these for backward compat; `i18n.ts` and `main.tsx` use them directly.
 */

export type UiLanguageCode = 'en' | 'ja' | 'zh-TW';

/**
 * Best-effort map of the browser's `navigator.language` onto the supported
 * UI-language set. Snapshotted at bootstrap as the user's initial choice; they
 * override it in Settings. Defaults to 'en' for any unsupported language (the
 * UI is English-only outside ja/zh-TW). Simplified Chinese (zh-CN / zh-Hans)
 * maps to 'en' — only Traditional (zh-TW / zh-Hant / zh-HK / zh-MO) is in scope.
 */
export function detectUiLanguage(): UiLanguageCode {
  try {
    const lang = (navigator.language || 'en').toLowerCase();
    if (lang.startsWith('ja')) return 'ja';
    if (lang.startsWith('zh')) {
      if (
        lang.includes('tw') ||
        lang.includes('hant') ||
        lang.includes('hk') ||
        lang.includes('mo')
      ) {
        return 'zh-TW';
      }
      return 'en';
    }
    return 'en';
  } catch {
    return 'en';
  }
}

/**
 * Resolve the *effective* UI language (for `<html lang>` and i18next chrome)
 * from the store's `uiLanguage`. An explicit choice ('en' | 'ja' | 'zh-TW')
 * wins; null/undefined (legacy user with no stored choice, or mid-boot before
 * /me resolves) falls back to the browser-detected language — matching both
 * `i18n.ts`'s initial language and `displayLocale()`'s null/undefined contract
 * (DESIGN.md §6.6). Mapping the unset case to 'en' would force English chrome
 * on a Japanese/Chinese browser whose `ui_language` is null.
 */
export function resolveUiLanguage(
  stored: UiLanguageCode | null | undefined,
): UiLanguageCode {
  if (stored === 'en' || stored === 'ja' || stored === 'zh-TW') return stored;
  return detectUiLanguage();
}
