import { X } from "lucide-react";
import { SketchIcon } from "@/components/SketchIcon";

interface Props {
  onDismiss: () => void;
}

/**
 * Amber-wash banner: AI is down but the dashboard still works.
 * Visually distinct from OfflineBanner (sunken/cool vs warn/warm).
 */
export function AIUnavailableBanner({ onDismiss }: Props) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-3 border-b border-warn/30 bg-warn-wash px-4 py-2.5 text-[0.8rem] text-ink-secondary"
    >
      <div className="relative mt-0.5 flex-shrink-0 text-warn">
        <SketchIcon kind="sparkle" size={14} seed={83} />
        {/* diagonal slash */}
        <span
          aria-hidden
          className="absolute inset-0 block"
          style={{
            background:
              "linear-gradient(to top right, transparent 45%, currentColor 47%, currentColor 53%, transparent 55%)",
          }}
        />
      </div>
      <p className="flex-1 leading-snug">
        ai is temporarily unavailable. your dashboard, cards, and edits all
        still work — only the chat assistant is down.
      </p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="dismiss"
        className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-ink-tertiary hover:bg-warn/15 hover:text-ink-secondary"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
