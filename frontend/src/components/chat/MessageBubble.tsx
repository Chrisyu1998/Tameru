import { cn } from "@/lib/utils";

interface MessageBubbleProps {
  role: "user" | "assistant";
  /** When false, render plain text on the canvas (no background). */
  bubble?: boolean;
  children: React.ReactNode;
  className?: string;
}

/**
 * Per spec: user bubbles sit right with a soft moss tint.
 * AI text sits left with NO background (just text).
 * AI cards (parse / candidates / chart) opt into a bubble container.
 */
export function MessageBubble({
  role,
  bubble = true,
  children,
  className,
}: MessageBubbleProps) {
  if (role === "user") {
    return (
      <div className="flex w-full justify-end animate-slide-up-in">
        <div
          className={cn(
            "max-w-[78%] rounded-2xl bg-moss-wash px-4 py-2.5 text-[0.95rem] leading-snug text-ink",
            className
          )}
        >
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="flex w-full justify-start animate-slide-up-in">
      <div
        className={cn(
          "max-w-[88%]",
          bubble &&
            "rounded-2xl border border-hairline bg-elevated px-4 py-3 text-[0.95rem] leading-relaxed text-ink",
          !bubble && "px-1 py-0 text-[0.95rem] leading-relaxed text-ink",
          className
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** Tiny tertiary attribution line shown below an AI message. */
export function ToolAttribution({ name }: { name: string }) {
  return (
    <div className="mt-1 flex w-full justify-start">
      <span className="inline-flex items-center gap-1.5 px-1 text-[0.7rem] text-ink-tertiary tracking-wide">
        <span aria-hidden className="inline-block h-1 w-1 rounded-full bg-moss" />
        via <span className="font-mono">{name}</span>
      </span>
    </div>
  );
}
