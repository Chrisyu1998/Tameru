import { useEffect, useRef, useState } from "react";
import { RotateCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface AutoLoggedBadgeProps {
  /** Default copy works for transactions; pass a custom message for other surfaces. */
  tooltip?: string;
  className?: string;
}

/**
 * Small 🔄 chip that flags content tameru auto-detected (recurring transactions,
 * inferred subscriptions). Click/tap to reveal a brief tooltip explaining
 * provenance — keeps users in the loop about what *they* logged vs. what
 * tameru inferred.
 */
export function AutoLoggedBadge({
  tooltip = "auto-logged by tameru — detected as a recurring charge.",
  className,
}: AutoLoggedBadgeProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  // Click-outside dismiss.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <span ref={wrapRef} className={cn("relative inline-flex", className)}>
      <button
        type="button"
        aria-label="auto-logged by tameru"
        onClick={(e) => {
          // Don't trigger the row's onClick (e.g. opening the edit sheet).
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-moss-wash text-moss-deep hover:bg-moss-wash/70"
      >
        <RotateCw className="h-2.5 w-2.5" />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-1/2 top-full z-20 mt-1.5 w-52 -translate-x-1/2 rounded-xl border border-hairline bg-elevated px-3 py-2 text-[0.72rem] leading-snug text-ink-secondary shadow-sm"
        >
          {tooltip}
        </span>
      )}
    </span>
  );
}
