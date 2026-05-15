import { cn } from "@/lib/utils";

type Direction = "above" | "below" | "usual" | "neutral";
type Layout = "row" | "stacked";
type Tone = "tinted" | "neutral";

interface DeltaTileProps {
  category: string;
  /** Signed delta amount, e.g. +47 or -22. Whole-currency units. */
  delta: number;
  /** Ordinal band copy: "above usual", "below usual", "as usual" */
  band?: string;
  currency?: string;
  layout?: Layout;
  tone?: Tone;
  /** Force a specific direction (e.g. "neutral" for a flat informational tile). */
  direction?: Direction;
  className?: string;
  onClick?: () => void;
}

function inferDirection(delta: number): Direction {
  if (delta > 0) return "above";
  if (delta < 0) return "below";
  return "usual";
}

const tintByDirection: Record<Direction, string> = {
  above: "bg-over-wash border-over/20",
  below: "bg-moss-wash border-moss-soft/40",
  usual: "bg-warn-wash/60 border-warn/20",
  neutral: "bg-sunken border-hairline",
};

// Muted brand backgrounds (used when tone="neutral" on home tiles).
// Lower alpha keeps the hue but lets the cream paper read through.
const neutralByDirection: Record<Direction, string> = {
  above: "bg-[#A0624A]/55 border-[#A0624A]/30",
  below: "bg-[#6F8368]/55 border-[#6F8368]/30",
  usual: "bg-[#C4873A]/55 border-[#C4873A]/30",
  neutral: "bg-ink/[0.06] border-hairline",
};

const textByDirection: Record<Direction, string> = {
  above: "text-over",
  below: "text-moss-deep",
  usual: "text-warn",
  neutral: "text-ink-secondary",
};

// On muted brand backgrounds, deep ink stays legible and feels calmer than paper.
const solidTextByDirection: Record<Direction, string> = {
  above: "text-ink",
  below: "text-ink",
  usual: "text-ink",
  neutral: "text-ink",
};

export function DeltaTile({
  category,
  delta,
  band,
  currency = "$",
  layout = "row",
  tone = "tinted",
  direction: directionOverride,
  className,
  onClick,
}: DeltaTileProps) {
  const direction = directionOverride ?? inferDirection(delta);
  const isSolid = tone === "neutral";
  const tileSurface = isSolid ? neutralByDirection : tintByDirection;
  const amountText = isSolid ? solidTextByDirection : textByDirection;
  const labelText = isSolid ? "text-ink/80" : "text-ink";
  const subText = isSolid ? "text-ink/55" : "text-ink-tertiary";
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  const abs = Math.abs(delta);
  const resolvedBand =
    band ??
    (direction === "above"
      ? "above usual"
      : direction === "below"
      ? "below usual"
      : direction === "neutral"
      ? "neutral"
      : "as usual");

  const Tag = onClick ? "button" : "div";

  if (layout === "stacked") {
    return (
      <Tag
        onClick={onClick}
        className={cn(
          "group relative flex w-full flex-col items-start justify-between rounded-2xl border px-4 py-4 text-left transition-colors min-h-[6.5rem]",
          tileSurface[direction],
          onClick && !isSolid && "hover:bg-elevated",
          onClick && isSolid && "hover:opacity-95",
          className
        )}
      >
        <span className={cn("font-serif text-[0.95rem] lowercase-title", labelText)}>
          {category}
        </span>
        <div className="mt-2 flex flex-col gap-0.5">
          <span
            className={cn(
              "tabular font-serif text-2xl leading-none",
              amountText[direction]
            )}
          >
            {sign}
            {currency}
            {abs}
          </span>
          <span className={cn("text-[0.72rem] lowercase tracking-wide", subText)}>
            {resolvedBand}
          </span>
        </div>
      </Tag>
    );
  }

  return (
    <Tag
      onClick={onClick}
      className={cn(
        "group relative flex w-full items-baseline justify-between rounded-2xl border px-4 py-3 text-left transition-colors",
        tileSurface[direction],
        onClick && !isSolid && "hover:bg-elevated",
        onClick && isSolid && "hover:opacity-95",
        className
      )}
    >
      <span className={cn("font-serif text-base lowercase-title", labelText)}>
        {category}
      </span>
      <span
        className={cn("tabular text-sm font-medium", amountText[direction])}
      >
        {sign}
        {currency}
        {abs}
        <span className={cn("ml-2 font-normal", subText)}>
          · {resolvedBand}
        </span>
      </span>
    </Tag>
  );
}
