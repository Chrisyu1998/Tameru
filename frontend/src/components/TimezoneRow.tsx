import { useEffect, useRef, useState } from "react";

import { detectTimezone } from "@/lib/auth";
import { readPreferences, updatePreferences } from "@/lib/preferencesApi";

const COMMON_TIMEZONES = [
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "Europe/London",
  "Europe/Paris",
  "Asia/Taipei",
  "Asia/Hong_Kong",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

/**
 * Per-user IANA timezone control (DESIGN.md §6.6). Drives weekly-digest
 * delivery time and week-boundary math. Shared between desktop Settings
 * → Notifications and the mobile More → Notifications sheet so a copy
 * or wiring tweak only has to land in one place (same doctrine as
 * AnalyticsOptOutToggle).
 *
 * Optimistic write + monotonic-sequence guard so rapid changes can't
 * let a stale PATCH response win.
 */
export function TimezoneRow() {
  const [tz, setTz] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const latestRequest = useRef(0);

  useEffect(() => {
    const myRequest = ++latestRequest.current;
    readPreferences()
      .then((prefs) => {
        if (myRequest !== latestRequest.current) return;
        setTz(prefs.timezone ?? detectTimezone());
      })
      .catch(() => {
        setTz(detectTimezone());
      });
  }, []);

  const options = (() => {
    const set = new Set(COMMON_TIMEZONES);
    const detected = detectTimezone();
    if (detected) set.add(detected);
    if (tz) set.add(tz);
    return Array.from(set).sort();
  })();

  const handleChange = (next: string) => {
    if (saving || next === tz) return;
    const prev = tz;
    setTz(next);
    setSaving(true);
    const myRequest = ++latestRequest.current;
    updatePreferences({ timezone: next })
      .then((prefs) => {
        if (myRequest !== latestRequest.current) return;
        setTz(prefs.timezone ?? next);
      })
      .catch(() => {
        if (myRequest !== latestRequest.current) return;
        setTz(prev);
      })
      .finally(() => {
        if (myRequest !== latestRequest.current) return;
        setSaving(false);
      });
  };

  return (
    <div className="flex flex-col gap-1 px-1 py-3">
      <span className="text-[0.95rem] text-ink">timezone</span>
      <span className="text-[0.78rem] text-ink-tertiary">
        when your digest arrives, and how dates line up. defaults to this
        device's timezone.
      </span>
      <select
        value={tz ?? ""}
        onChange={(e) => handleChange(e.target.value)}
        disabled={saving || tz === null}
        className="mt-2 h-10 w-full rounded-2xl border border-hairline bg-elevated px-3 text-sm text-ink focus:outline-none disabled:opacity-60"
        data-testid="timezone-select"
      >
        {tz === null && <option value="">loading…</option>}
        {options.map((z) => (
          <option key={z} value={z}>
            {z.replace(/_/g, " ")}
          </option>
        ))}
      </select>
    </div>
  );
}
