import { useTranslation } from "react-i18next";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * Per-user light/dark appearance control. Unlike the other preference rows
 * (LanguageRow / TimezoneRow), the theme lives in localStorage via
 * `useTheme` — there is no server round-trip, so no optimistic-write /
 * monotonic-sequence guard is needed. Rendered in desktop Settings →
 * Account and the mobile More → appearance sheet.
 *
 * This replaced the global floating `ThemeToggle` (a `fixed top-4 right-4`
 * button mounted on every surface), which collided with per-page top-right
 * controls — most visibly the chat page's new-chat button. Theme is a
 * set-once preference, so Settings is its natural home.
 */
export function ThemeRow() {
  const { theme, toggle } = useTheme();
  const { t } = useTranslation();
  const isDark = theme === "dark";

  return (
    <div className="flex items-center justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <p className="text-[0.95rem] text-ink lowercase-title">
          {t("settings.theme.label")}
        </p>
        <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">
          {t("settings.theme.desc")}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={isDark}
        aria-label={
          isDark
            ? t("settings.theme.switchToLight")
            : t("settings.theme.switchToDark")
        }
        onClick={toggle}
        className={cn(
          "relative h-6 w-10 flex-shrink-0 rounded-full transition-colors",
          isDark ? "bg-moss" : "bg-sunken"
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-surface shadow transition-all",
            isDark ? "left-[1.125rem]" : "left-0.5"
          )}
        />
      </button>
    </div>
  );
}
