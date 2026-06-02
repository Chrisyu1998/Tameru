import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { RowActions } from "@/components/desktop/RowActions";

interface SwipeableRowProps {
  children: ReactNode;
  onConfirmDelete: () => void;
  /** Desktop-only: optional edit handler shown in the hover RowActions overlay. */
  onEdit?: () => void;
  /** How wide (px) the delete panel is. */
  panelWidth?: number;
  /** Min swipe (px) before the panel locks open. */
  threshold?: number;
}

/**
 * Two-stage swipe-to-delete on mobile (≤md):
 *   1. Swipe left to reveal the labeled "Delete" panel (locks once threshold passed)
 *   2. Tap the panel to confirm
 *
 * On desktop (md+) we additionally render a hover/focus-revealed RowActions
 * overlay (edit + delete + faint kebab hint). Swipe still works for trackpads.
 *
 * Tapping anywhere else (or swiping right) collapses the panel.
 */
export function SwipeableRow({
  children,
  onConfirmDelete,
  onEdit,
  panelWidth = 96,
  threshold = 48,
}: SwipeableRowProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [dragX, setDragX] = useState(0);
  const startX = useRef<number | null>(null);
  const startedHorizontal = useRef(false);
  const startY = useRef<number | null>(null);

  const offset = open ? -panelWidth : Math.min(0, dragX);

  // Click-outside collapse
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const onPointerDown = (e: React.PointerEvent) => {
    startX.current = e.clientX;
    startY.current = e.clientY;
    startedHorizontal.current = false;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (startX.current === null || startY.current === null) return;
    const dx = e.clientX - startX.current;
    const dy = e.clientY - startY.current;
    if (!startedHorizontal.current) {
      if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
      startedHorizontal.current = Math.abs(dx) > Math.abs(dy);
      if (!startedHorizontal.current) {
        // vertical scroll — abandon
        startX.current = null;
        return;
      }
    }
    // Only allow leftward drag, with mild rubberband if already open or pulling right
    const next = Math.max(-panelWidth - 24, Math.min(0, dx + (open ? -panelWidth : 0)));
    setDragX(next);
  };

  const onPointerUp = () => {
    if (startX.current === null) return;
    const distance = Math.abs(dragX);
    if (open) {
      setOpen(distance > panelWidth - threshold);
    } else {
      setOpen(distance >= threshold);
    }
    setDragX(0);
    startX.current = null;
    startY.current = null;
  };

  return (
    <div
      ref={containerRef}
      className="group relative overflow-hidden rounded-2xl bg-surface"
    >
      {/* Delete panel sits behind the row, revealed as it slides left (mobile) */}
      <button
        type="button"
        onClick={() => {
          onConfirmDelete();
          setOpen(false);
        }}
        aria-label={t("common.confirmDelete")}
        className={cn(
          "absolute inset-y-0 right-0 flex items-center justify-center bg-over text-surface transition-opacity md:hidden",
          open ? "opacity-100" : "opacity-90"
        )}
        style={{ width: panelWidth }}
      >
        <span className="text-sm font-medium tracking-wide">{t("common.delete")}</span>
      </button>

      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        className={cn(
          "relative bg-surface touch-pan-y md:pr-16",
          startedHorizontal.current ? "" : "transition-transform duration-200 ease-out"
        )}
        style={{ transform: `translateX(${offset}px)` }}
      >
        {children}
      </div>

      {/* Desktop hover/focus row actions */}
      <RowActions onEdit={onEdit} onDelete={onConfirmDelete} />
    </div>
  );
}
