import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface RowActionsProps {
  onEdit?: () => void;
  onDelete?: () => void;
  /** Stick to the right edge of a row. Parent must be `group` + `relative`. */
  className?: string;
}

/**
 * Desktop hover/focus row actions. Replaces swipe gestures.
 * Faint always-visible kebab dot hints touchpad users that more actions exist.
 * Edit + delete icons reveal on group-hover or keyboard focus.
 */
export function RowActions({ onEdit, onDelete, className }: RowActionsProps) {
  return (
    <div
      className={cn(
        "absolute right-2 top-1/2 hidden -translate-y-1/2 items-center md:flex",
        className
      )}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Faint persistent hint */}
      <span className="pointer-events-none inline-flex items-center text-ink-quaternary opacity-50 transition-opacity duration-150 group-hover:opacity-0 group-focus-within:opacity-0">
        <MoreHorizontal className="h-4 w-4" />
      </span>

      {/* Revealed actions */}
      <div className="absolute right-0 flex items-center gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
        {onEdit && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onEdit();
            }}
            aria-label="edit"
            className="flex h-7 w-7 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink focus:outline-none focus:bg-sunken/60 focus:text-ink"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
        )}
        {onDelete && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            aria-label="delete"
            className="flex h-7 w-7 items-center justify-center rounded-full text-ink-tertiary hover:bg-over-wash hover:text-over focus:outline-none focus:bg-over-wash focus:text-over"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}
