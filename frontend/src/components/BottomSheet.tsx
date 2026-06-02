import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
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
  // Portal to body so the fixed positioning is anchored to the viewport
  // regardless of any ancestor `transform`/`filter`/`will-change` (which
  // would otherwise become the containing block — animate-fade-up on
  // page wrappers was clipping the sheet and breaking internal scroll).
  if (desktopVariant === "side") {
    return createPortal(
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
              aria-label={t("common.close")}
              className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          )}
          <div className={cn("min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 pt-12 pb-8", className)}>
            {children}
          </div>
        </aside>
      </>,
      document.body
    );
  }

  // ── Default: bottom sheet on mobile, centered dialog on md+ ──────────
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      className="fixed inset-0 z-[100] flex items-end justify-center md:items-center"
    >
      <button
        type="button"
        aria-label={t("common.closeSheet")}
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
          "relative flex w-full max-w-md flex-col overflow-hidden border border-hairline bg-elevated",
          // Cap at 85% of dynamic viewport (svh handles iOS URL bar collapse)
          // so tall sheets don't run off the top of the screen — content
          // inside scrolls instead.
          "max-h-[85svh]",
          // Mobile: bottom sheet
          "rounded-t-[2rem] animate-sheet-up",
          // Desktop: centered dialog
          "md:rounded-3xl md:animate-dialog-in md:max-w-lg md:mx-4",
          className
        )}
      >
        {/* Mobile drag handle (hidden on desktop) */}
        <div className="flex shrink-0 items-center justify-center pt-3 pb-2 md:hidden">
          <span className="block h-1 w-10 rounded-full bg-ink-quaternary/60" />
        </div>

        {!blockDismiss && (
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-2 pb-8 md:pt-6 md:pb-7">
          {children}
        </div>
      </div>
    </div>,
    document.body
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
  const { t } = useTranslation();
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      className="fixed inset-0 z-[100] flex items-end justify-center md:hidden"
    >
      <button
        type="button"
        aria-label={t("common.closeSheet")}
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
          "relative flex w-full max-w-md flex-col overflow-hidden rounded-t-[2rem] border border-hairline bg-elevated animate-sheet-up",
          "max-h-[85svh]",
          className
        )}
      >
        <div className="flex shrink-0 items-center justify-center pt-3 pb-2">
          <span className="block h-1 w-10 rounded-full bg-ink-quaternary/60" />
        </div>
        {!blockDismiss && (
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-2 pb-8">
          {children}
        </div>
      </div>
    </div>
  );
}
