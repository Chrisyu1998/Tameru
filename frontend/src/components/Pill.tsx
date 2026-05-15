import { cn } from "@/lib/utils";

type PillTone =
  | "neutral"
  | "moss"
  | "warn"
  | "over"
  | "ink";

interface PillProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: PillTone;
}

const toneClasses: Record<PillTone, string> = {
  neutral: "bg-sunken text-ink-secondary",
  moss: "bg-moss-wash text-moss-deep",
  warn: "bg-warn-wash text-warn",
  over: "bg-warn-wash/60 text-over",
  ink: "bg-ink/10 text-ink",
};

export function Pill({ tone = "neutral", className, ...props }: PillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[0.72rem] font-medium tracking-wide",
        toneClasses[tone],
        className
      )}
      {...props}
    />
  );
}
