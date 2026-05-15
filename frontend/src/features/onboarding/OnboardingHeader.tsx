import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";

interface OnboardingHeaderProps {
  onBack?: () => void;
  className?: string;
}

/** Floating back affordance for steps that allow backward nav. */
export function OnboardingHeader({ onBack, className }: OnboardingHeaderProps) {
  if (!onBack) return null;
  return (
    <div className={cn("fixed left-3 top-3 z-40", className)}>
      <button
        type="button"
        onClick={onBack}
        aria-label="back"
        className="flex h-10 w-10 items-center justify-center rounded-full text-ink-secondary transition-colors hover:bg-sunken/60 hover:text-ink"
      >
        <ArrowLeft className="h-4 w-4" />
      </button>
    </div>
  );
}
