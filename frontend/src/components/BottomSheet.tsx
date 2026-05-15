import { useEffect } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  /** When true, the only way to close is via an explicit confirm action inside children */
  blockDismiss?: boolean;
  className?: string;
  ariaLabel?: string;
  /**
   * Desktop layout variant.
   *  - "dialog" (default): centered modal on md+
   *  - "side":   right-side panel aligned with the chat drawer (no scrim)
   * Mobile (<md) is always a bottom sheet regardless.
   */
  desktopVariant?: "dialog" | "side";
}

export function BottomSheet({
  open,
  onClose,
  children,
  blockDismiss = false,
  className,
  ariaLabel = "sheet",
  desktopVariant = "dialog",
}: BottomSheetProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !blockDismiss) onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, blockDismiss, onClose]);

  if (!open) return null;

  // ── Desktop "side" variant: right-side panel, no scrim ──────────────
  if (desktopVariant === "side") {
    return (
      <>
        {/* Mobile (<md) keeps the bottom sheet treatment */}
        <MobileBottomSheet
          ariaLabel={ariaLabel}
          blockDismiss={blockDismiss}
          onClose={onClose}
          className={className}
        >
          {children}
        </MobileBottomSheet>

        {/* Desktop side panel — no scrim, lives above the drawer */}
        <aside
          role="dialog"
          aria-modal="false"
          aria-label={ariaLabel}
          className={cn(
            "fixed top-0 right-0 z-[110] hidden h-screen w-[33%] min-w-[400px] md:flex flex-col",
            "border-l border-hairline bg-elevated animate-slide-in-right"
          )}
        >
          {!blockDismiss && (
            <button
              type="button"
              onClick={onClose}
              aria-label="close"
              className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          )}
          <div className={cn("flex-1 overflow-y-auto px-6 pt-12 pb-8", className)}>
            {children}
          </div>
        </aside>
      </>
    );
  }

  // ── Default: bottom sheet on mobile, centered dialog on md+ ──────────
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      className="fixed inset-0 z-[100] flex items-end justify-center md:items-center"
    >
      <button
        type="button"
        aria-label="close sheet"
        onClick={() => {
          if (!blockDismiss) onClose();
        }}
        className={cn(
          "absolute inset-0 bg-ink/40 backdrop-blur-[2px] animate-scrim-in",
          blockDismiss && "cursor-default"
        )}
        tabIndex={blockDismiss ? -1 : 0}
      />

      <div
        className={cn(
          "relative w-full max-w-md border border-hairline bg-elevated pb-8 pt-3",
          // Mobile: bottom sheet
          "rounded-t-[2rem] animate-sheet-up",
          // Desktop: centered dialog
          "md:rounded-3xl md:pt-6 md:pb-7 md:animate-dialog-in md:max-w-lg md:mx-4",
          className
        )}
      >
        {/* Mobile drag handle (hidden on desktop) */}
        <div className="flex items-center justify-center pb-2 md:hidden">
          <span className="block h-1 w-10 rounded-full bg-ink-quaternary/60" />
        </div>

        {!blockDismiss && (
          <button
            type="button"
            onClick={onClose}
            aria-label="close"
            className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        )}

        <div className="px-5 pt-2 md:pt-0">{children}</div>
      </div>
    </div>
  );
}

/* Internal: mobile-only bottom sheet, used when the desktop side variant is
   active so phones still get the right treatment. */
function MobileBottomSheet({
  children,
  blockDismiss,
  onClose,
  className,
  ariaLabel,
}: {
  children: React.ReactNode;
  blockDismiss: boolean;
  onClose: () => void;
  className?: string;
  ariaLabel: string;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      className="fixed inset-0 z-[100] flex items-end justify-center md:hidden"
    >
      <button
        type="button"
        aria-label="close sheet"
        onClick={() => {
          if (!blockDismiss) onClose();
        }}
        className={cn(
          "absolute inset-0 bg-ink/40 backdrop-blur-[2px] animate-scrim-in",
          blockDismiss && "cursor-default"
        )}
        tabIndex={blockDismiss ? -1 : 0}
      />
      <div
        className={cn(
          "relative w-full max-w-md rounded-t-[2rem] border border-hairline bg-elevated pb-8 pt-3 animate-sheet-up",
          className
        )}
      >
        <div className="flex items-center justify-center pb-2">
          <span className="block h-1 w-10 rounded-full bg-ink-quaternary/60" />
        </div>
        {!blockDismiss && (
          <button
            type="button"
            onClick={onClose}
            aria-label="close"
            className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        )}
        <div className="px-5 pt-2">{children}</div>
      </div>
    </div>
  );
}
