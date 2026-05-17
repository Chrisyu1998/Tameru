import { useEffect, useState } from "react";

/**
 * Thin moss progress bar pinned to the bottom of a pending-delete row.
 * Animated client-side via requestAnimationFrame off `scheduledAt`; the
 * actual commit is driven by the ledger's module-level setTimeout, so
 * this is purely a visual countdown. Used by both the transactions
 * breakdown list and the cards list — same shape, same UX.
 */
export function PendingDeleteProgress({
  scheduledAt,
  durationMs,
}: {
  scheduledAt: number;
  durationMs: number;
}) {
  const [progress, setProgress] = useState(() =>
    Math.min(1, (Date.now() - scheduledAt) / durationMs),
  );
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const elapsed = Date.now() - scheduledAt;
      const next = Math.min(1, elapsed / durationMs);
      setProgress(next);
      if (next < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [scheduledAt, durationMs]);
  return (
    <div className="pointer-events-none absolute bottom-0 left-0 right-0 h-0.5 bg-hairline">
      <div
        className="h-full bg-moss"
        style={{ width: `${progress * 100}%` }}
      />
    </div>
  );
}
