import { useRef, useState } from "react";

import { detectUiLanguage } from "@/lib/auth";
import { updatePreferences } from "@/lib/preferencesApi";
import { useAppStore, type UiLanguage } from "@/store";

/**
 * The supported UI languages, each labelled in its own script so a user who
 * can't read the current UI language can still find their own (DESIGN.md
 * §6.6 Tier 2). Traditional Chinese only — Simplified is out of scope.
 */
const LANGUAGE_OPTIONS: { value: "en" | "ja" | "zh-TW"; label: string }[] = [
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
  { value: "zh-TW", label: "繁體中文" },
];

/**
 * Per-user UI/display-language control (DESIGN.md §6.6 Tier 2). Drives the
 * formatting locale (`displayLocale()`), category display labels, the chat
 * agent's reply language, and the weekly digest language. The third i18n
 * axis — independent of currency and timezone.
 *
 * Writes both the store (so `displayLocale()` and reactive label consumers
 * pick the change up immediately) and `users_meta.ui_language` via PATCH.
 * Optimistic write + monotonic-sequence guard so rapid changes can't let a
 * stale PATCH response win — same shape as TimezoneRow / AnalyticsOptOutToggle.
 */
export function LanguageRow() {
  const stored = useAppStore((s) => s.uiLanguage);
  const setUiLanguage = useAppStore((s) => s.setUiLanguage);
  const [saving, setSaving] = useState(false);
  const latestRequest = useRef(0);

  // Resolve the displayed value: the explicit stored choice, or the browser
  // best-guess until the user picks one. `undefined` (still booting) also
  // falls back to the detected language so the select is never empty.
  const current: "en" | "ja" | "zh-TW" =
    stored === "en" || stored === "ja" || stored === "zh-TW"
      ? stored
      : detectUiLanguage();

  const handleChange = (next: UiLanguage) => {
    if (saving || next === stored) return;
    if (next !== "en" && next !== "ja" && next !== "zh-TW") return;
    const prev = stored;
    setUiLanguage(next);
    setSaving(true);
    const myRequest = ++latestRequest.current;
    updatePreferences({ ui_language: next })
      .then((prefs) => {
        if (myRequest !== latestRequest.current) return;
        setUiLanguage(prefs.ui_language);
      })
      .catch(() => {
        if (myRequest !== latestRequest.current) return;
        setUiLanguage(prev);
      })
      .finally(() => {
        if (myRequest !== latestRequest.current) return;
        setSaving(false);
      });
  };

  return (
    <div className="flex flex-col gap-1 px-1 py-3">
      <span className="text-[0.95rem] text-ink">language</span>
      <span className="text-[0.78rem] text-ink-tertiary">
        how tameru talks to you — the interface, chat replies, and your weekly
        digest. defaults to this device's language.
      </span>
      <select
        value={current}
        onChange={(e) => handleChange(e.target.value as UiLanguage)}
        disabled={saving}
        className="mt-2 h-10 w-full rounded-2xl border border-hairline bg-elevated px-3 text-sm text-ink focus:outline-none disabled:opacity-60"
        data-testid="language-select"
      >
        {LANGUAGE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
