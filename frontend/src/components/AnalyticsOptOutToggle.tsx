import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { identifyUser, setOptOut } from "@/lib/analytics";
import { updatePreferences } from "@/lib/preferencesApi";
import { useAppStore } from "@/store";
import { cn } from "@/lib/utils";

/**
 * Shared "pause product analytics" toggle. Rendered on both `/settings`
 * (desktop privacy section) and `/privacy` (the route the mobile More
 * menu links to). Extracting it keeps the two surfaces in lockstep so
 * a future copy/wiring tweak doesn't have to be applied twice.
 *
 * Optimistic-write + monotonic-sequence pattern so rapid toggling
 * can't strand the UI on a stale state. The PostHog SDK is updated in
 * lockstep with the server PATCH via setOptOut(); on opt-in we also
 * re-bind the user id via identifyUser() (Codex 2026-05-23 P3).
 *
 * Source of truth for the initial value is /me, mirrored into the
 * Zustand store by auth.refreshHomeCurrency(), so we render
 * synchronously from the store. A PATCH read-back is unnecessary on
 * mount.
 */
export function AnalyticsOptOutToggle() {
  const optedOutFromStore = useAppStore((s) => s.analyticsOptedOut);
  const setOptedOutInStore = useAppStore((s) => s.setAnalyticsOptedOut);
  const userId = useAppStore((s) => s.user?.id ?? null);
  const initial = optedOutFromStore ?? false;
  const [optedOut, setLocalOptedOut] = useState(initial);
  const [saving, setSaving] = useState(false);
  const latestRequest = useRef(0);

  // Reconcile if /me resolves after this component first rendered.
  useEffect(() => {
    if (typeof optedOutFromStore === "boolean") {
      setLocalOptedOut(optedOutFromStore);
    }
  }, [optedOutFromStore]);

  /**
   * Apply the new opt-out state to the SDK. On opt-in, also re-bind
   * the current user's id — `setOptOut(true)` calls `posthog.reset()`
   * which rotates the anonymous distinct id, so without re-identifying
   * here, subsequent events would be captured anonymously until a
   * page reload triggered another /me → identifyUser().
   */
  const applyOptOutAndIdentify = (nextOptedOut: boolean) => {
    setOptOut(nextOptedOut);
    if (!nextOptedOut && userId) {
      identifyUser(userId);
    }
  };

  const handleChange = (next: boolean) => {
    if (saving) return;
    setLocalOptedOut(next);
    setSaving(true);
    setOptedOutInStore(next);
    // Flip the SDK in lockstep so an outbound event mid-PATCH doesn't
    // leak past the user's choice. On failure we revert both.
    applyOptOutAndIdentify(next);
    const myRequest = ++latestRequest.current;
    updatePreferences({ analytics_opted_out: next })
      .then((prefs) => {
        if (myRequest !== latestRequest.current) return;
        setLocalOptedOut(prefs.analytics_opted_out);
        setOptedOutInStore(prefs.analytics_opted_out);
        applyOptOutAndIdentify(prefs.analytics_opted_out);
      })
      .catch(() => {
        if (myRequest !== latestRequest.current) return;
        setLocalOptedOut(!next);
        setOptedOutInStore(!next);
        applyOptOutAndIdentify(!next);
      })
      .finally(() => {
        if (myRequest !== latestRequest.current) return;
        setSaving(false);
      });
  };

  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <p className="text-[0.95rem] text-ink lowercase-title">
          {t("privacy.analytics.label")}
        </p>
        <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">
          {t("privacy.analytics.desc")}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={optedOut}
        aria-disabled={saving}
        disabled={saving}
        onClick={() => handleChange(!optedOut)}
        className={cn(
          "relative h-6 w-10 flex-shrink-0 rounded-full transition-colors",
          optedOut ? "bg-moss" : "bg-sunken",
          saving && "opacity-50 cursor-not-allowed",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-surface shadow transition-all",
            optedOut ? "left-[1.125rem]" : "left-0.5",
          )}
        />
      </button>
    </div>
  );
}
