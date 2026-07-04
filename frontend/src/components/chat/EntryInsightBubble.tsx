import { AlertTriangle, TrendingDown, TrendingUp } from "lucide-react";
import type { InsightSeverity } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { MessageBubble } from "./MessageBubble";

/**
 * Entry-moment insight bubble — Day 13; tiered severity added 2026-05-20,
 * positive tier added 2026-07-03 (DESIGN.md §6.2).
 *
 * Rendered below a committed parse card when `POST /transactions/confirm`
 * returns a non-null `insight`. The sentence and `severity` are produced
 * by the deterministic rule engine in `app/services/entry_moment.py`;
 * this component only displays them.
 *
 * Four tiers, mirroring the §6.3 dashboard baseline color scale:
 *   - `calm`     — a quiet grey italic aside (rules 1 / 2 / 4 + warm-up
 *                  rules 5 / 6).
 *   - `positive` — moss/green wash + downward glyph; the pace-aware rule 7,
 *                  comfortably under the category baseline ("you're okay").
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

  // Non-calm tiers get a louder, distinct treatment: a tinted wash, a
  // matching border, a leading glyph, and medium weight — so a "you're on
  // pace to overspend" (or the green "you're under") insight does not read
  // like a calm "biggest dinner this month" aside. The tier drives glyph +
  // color from one map so the three loud tiers stay visually parallel.
  const tier = TIERS[severity];
  return (
    <div className="flex w-full justify-start animate-slide-up-in">
      <div
        className={cn(
          "flex max-w-[88%] items-start gap-2.5 rounded-2xl border px-4 py-3",
          "text-[0.95rem] font-medium leading-relaxed text-ink",
          tier.box,
        )}
      >
        <tier.Icon aria-hidden className={cn("mt-0.5 h-4 w-4 shrink-0", tier.icon)} />
        <span>{text}</span>
      </div>
    </div>
  );
}

/**
 * Visual config for the three loud tiers, keyed by severity. `calm` is
 * handled separately (plain italic bubble), so it is intentionally absent.
 */
const TIERS: Record<
  Exclude<InsightSeverity, "calm">,
  { Icon: typeof TrendingUp; box: string; icon: string }
> = {
  positive: { Icon: TrendingDown, box: "border-moss bg-moss-wash", icon: "text-moss" },
  elevated: { Icon: TrendingUp, box: "border-warn bg-warn-wash", icon: "text-warn" },
  alert: { Icon: AlertTriangle, box: "border-over bg-over-wash", icon: "text-over" },
};
