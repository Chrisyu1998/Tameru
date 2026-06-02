/** A 5-second sonner-free undo toast queue, dedicated to ledger deletes. */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Undo2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PendingDelete {
  id: string;
  label: string;
  /** Called when the timer expires without undo. */
  commit: () => void;
}

interface UndoToastProps {
  pending: PendingDelete | null;
  onUndo: () => void;
  onTimeout: () => void;
  durationMs?: number;
}

export function UndoToast({
  pending,
  onUndo,
  onTimeout,
  durationMs = 5000,
}: UndoToastProps) {
  const { t } = useTranslation();
  const [progress, setProgress] = useState(1);

  useEffect(() => {
    if (!pending) return;
    setProgress(1);
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      setProgress(1 - t);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const timeout = setTimeout(() => {
      pending.commit();
      onTimeout();
    }, durationMs);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timeout);
    };
  }, [pending, durationMs, onTimeout]);

  if (!pending) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed bottom-24 md:bottom-6 left-1/2 z-[90] w-[min(92vw,22rem)] -translate-x-1/2",
        "overflow-hidden rounded-2xl border border-hairline bg-elevated animate-slide-up-in"
      )}
    >
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="flex flex-col leading-tight min-w-0">
          <span className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
            {t("common.undoToast.removing")}
          </span>
          <span className="truncate text-sm text-ink">{pending.label}</span>
        </div>
        <button
          type="button"
          onClick={onUndo}
          className="inline-flex items-center gap-1.5 rounded-full bg-moss px-3 py-1.5 text-xs font-medium text-surface hover:bg-moss-deep"
        >
          <Undo2 className="h-3 w-3" />
          {t("common.undoToast.undo")}
        </button>
      </div>
      <div className="h-0.5 bg-hairline">
        <div
          className="h-full bg-moss transition-[width] duration-75"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    </div>
  );
}
