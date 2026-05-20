import { AlertTriangle, TrendingUp } from "lucide-react";
import type { InsightSeverity } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { MessageBubble } from "./MessageBubble";

/**
 * Entry-moment insight bubble — Day 13; tiered severity added 2026-05-20
 * (DESIGN.md §6.2).
 *
 * Rendered below a committed parse card when `POST /transactions/confirm`
 * returns a non-null `insight`. The sentence and `severity` are produced
 * by the deterministic rule engine in `app/services/entry_moment.py`;
 * this component only displays them.
 *
 * Three tiers, mirroring the §6.3 dashboard baseline color scale:
 *   - `calm`     — a quiet grey italic aside (rules 1 / 2 / 4).
 *   - `elevated` — amber wash + glyph; the pace-aware overspending rule,
 *                  10-25% over the category baseline.
 *   - `alert`    — terracotta wash + warning glyph; the same rule, 25%+.
 *
 * No buttons, no dismiss × — rate limits handle fatigue server-side. A
 * leading glyph is not an action affordance, so the no-action spec still
 * holds. Never a modal, never a toast.
 */
export function EntryInsightBubble({
  text,
  severity,
}: {
  text: string;
  severity: InsightSeverity;
}) {
  if (severity === "calm") {
    return (
      <div className="flex w-full justify-start animate-slide-up-in">
        <MessageBubble
          role="assistant"
          bubble
          className="italic text-ink-secondary"
        >
          {text}
        </MessageBubble>
      </div>
    );
  }

  // Elevated / alert tiers get a louder, distinct treatment: a tinted
  // wash, a matching border, a leading glyph, and medium weight — so a
  // "you're on pace to overspend" insight does not read like a calm
  // "biggest dinner this month" aside.
  const isAlert = severity === "alert";
  const Icon = isAlert ? AlertTriangle : TrendingUp;
  return (
    <div className="flex w-full justify-start animate-slide-up-in">
      <div
        className={cn(
          "flex max-w-[88%] items-start gap-2.5 rounded-2xl border px-4 py-3",
          "text-[0.95rem] font-medium leading-relaxed text-ink",
          isAlert ? "border-over bg-over-wash" : "border-warn bg-warn-wash",
        )}
      >
        <Icon
          aria-hidden
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0",
            isAlert ? "text-over" : "text-warn",
          )}
        />
        <span>{text}</span>
      </div>
    </div>
  );
}
