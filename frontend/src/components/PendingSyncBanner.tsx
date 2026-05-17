import { useEffect, useState } from "react";
import { CloudOff, RefreshCw } from "lucide-react";
import {
  getPendingCount,
  subscribe as subscribeQueue,
} from "@/lib/offline_queue";

/**
 * Pending-sync banner (UX frame 32).
 *
 * Persistent micro-banner shown while the offline confirm queue has
 * entries for the currently signed-in user. Subscribes to the queue's
 * pub/sub so the count updates the moment an entry is enqueued (offline
 * tap on "looks right") or dequeued (drain success / permanent failure).
 *
 * Mounted at the layout level so the banner follows the user across
 * pages — the queue is global, not tied to the chat surface. Renders
 * nothing when count is 0.
 *
 * The visual treatment intentionally mirrors `UpdateToast`'s bottom-
 * pinned card so the two never compete for the same visual real estate
 * (the SW update toast is at `bottom-4`; this banner sits at `bottom-20`
 * to clear the BottomNav on mobile while UpdateToast pushes it up when
 * both are present).
 */
export function PendingSyncBanner() {
  const [count, setCount] = useState<number>(getPendingCount());

  useEffect(() => {
    const unsubscribe = subscribeQueue(() => {
      setCount(getPendingCount());
    });
    // Re-read on mount in case the queue's `refreshCount` populated the
    // cache between module init and the effect firing.
    setCount(getPendingCount());
    return unsubscribe;
  }, []);

  // Optional: when navigator goes online/offline, swap the icon so the
  // user gets a hint about why the queue isn't draining. We mirror
  // `navigator.onLine` reactively via the same window events the queue
  // drains on, so the icon stays in sync without an extra subscription.
  const [online, setOnline] = useState<boolean>(
    typeof navigator === "undefined" ? true : navigator.onLine,
  );
  useEffect(() => {
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  if (count === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-20 z-40 flex justify-center px-4"
    >
      <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-hairline bg-elevated px-3 py-1.5 text-[0.72rem] text-ink-secondary shadow-sm">
        {online ? (
          <RefreshCw className="h-3 w-3 animate-spin" aria-hidden />
        ) : (
          <CloudOff className="h-3 w-3" aria-hidden />
        )}
        <span className="tabular">
          {count} pending sync{count === 1 ? "" : "s"}
        </span>
      </div>
    </div>
  );
}
