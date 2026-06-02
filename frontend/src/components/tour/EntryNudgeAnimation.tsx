import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { tourEntryNudge } from "@/fixtures/tour";
import { useCategoryLabel } from "@/lib/categories";
import { cn } from "@/lib/utils";

/**
 * 4-beat animation that demonstrates Tameru's chat-based entry flow plus
 * the entry-moment insight. Drives Screen 2 of the guided tour (Day 21).
 *
 * Beats:
 *   1. user message bubble appears
 *   2. parse-card preview slides in below
 *   3. "looks right" button briefly highlights, then the parse card is
 *      replaced by a confirmed-transaction line
 *   4. quiet AI insight bubble fades in below the confirmed line
 *
 * The cycle loops every ~7s so a lingering user sees the beat repeat.
 * Each beat appends to the rendered list, so existing elements stay put
 * — no element ever animates out mid-cycle, only the full cycle resets
 * via a key bump on the wrapper.
 */
export function EntryNudgeAnimation() {
  // `cycle` forces a full remount each time the loop restarts, which
  // cleanly retriggers every entry animation without per-element state.
  const [cycle, setCycle] = useState(0);

  useEffect(() => {
    const t = window.setTimeout(() => setCycle((c) => c + 1), 7000);
    return () => window.clearTimeout(t);
  }, [cycle]);

  return <NudgeCycle key={cycle} />;
}

function NudgeCycle() {
  const { t } = useTranslation();
  const catLabel = useCategoryLabel();
  // Beat advances on a timer. Each beat reveals one more element.
  // Beat values: 0 = nothing, 1 = user bubble, 2 = +parse card,
  // 3 = +looks-right highlight, 4 = confirmed line replaces parse card,
  // 5 = +insight bubble.
  const [beat, setBeat] = useState(0);

  useEffect(() => {
    const schedule: Array<[number, number]> = [
      [400, 1], // user bubble
      [1200, 2], // parse card
      [2700, 3], // looks-right highlights
      [3400, 4], // confirmed line replaces parse card
      [4200, 5], // insight bubble
    ];
    const timers = schedule.map(([ms, value]) =>
      window.setTimeout(() => setBeat(value), ms)
    );
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, []);

  const showUser = beat >= 1;
  const showParse = beat >= 2 && beat < 4;
  const highlightButton = beat === 3;
  const showConfirmed = beat >= 4;
  const showInsight = beat >= 5;

  return (
    <div className="flex flex-col gap-3 min-h-[360px]">
      {showUser && (
        <MessageBubble role="user">
          {tourEntryNudge.userMessage}
        </MessageBubble>
      )}

      {showParse && (
        <div className="animate-slide-up-in rounded-2xl border border-hairline bg-elevated p-4">
          <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
            {t("tour.entry.doesThisLookRight")}
          </p>
          <div className="mt-3 flex flex-col gap-1.5 text-sm">
            <ParseRow label={t("tour.entry.merchant")} value={tourEntryNudge.parseCard.merchant} />
            <ParseRow label={t("tour.entry.amount")} value={tourEntryNudge.parseCard.amount} />
            <ParseRow label={t("tour.entry.category")} value={catLabel(tourEntryNudge.parseCard.category)} />
            <ParseRow label={t("tour.entry.card")} value={tourEntryNudge.parseCard.card} />
            <ParseRow label={t("tour.entry.date")} value={tourEntryNudge.parseCard.date} />
          </div>
          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              disabled
              className={cn(
                "rounded-full px-4 py-1.5 text-xs font-medium transition-all duration-300",
                highlightButton
                  ? "bg-moss-deep text-surface ring-4 ring-moss/30 scale-105"
                  : "bg-moss text-surface"
              )}
            >
              {t("tour.entry.looksRight")}
            </button>
            <button
              type="button"
              disabled
              className="rounded-full border border-hairline px-4 py-1.5 text-xs text-ink-secondary"
            >
              {t("tour.entry.fixIt")}
            </button>
          </div>
        </div>
      )}

      {showConfirmed && (
        <div className="animate-slide-up-in rounded-2xl border border-hairline bg-surface px-4 py-3">
          <div className="flex items-center gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-moss-wash text-moss-deep">
              <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
            </span>
            <div className="flex flex-1 items-baseline justify-between gap-2">
              <span className="text-sm text-ink">
                {tourEntryNudge.confirmedLine.merchant}
              </span>
              <span className="tabular text-sm text-ink">
                {tourEntryNudge.confirmedLine.amount}
              </span>
            </div>
          </div>
          <p className="mt-1 ml-9 text-[0.7rem] text-ink-tertiary">
            {catLabel(tourEntryNudge.confirmedLine.category).toLowerCase()} · {t("tour.entry.logged")}
          </p>
        </div>
      )}

      {showInsight && (
        <div className="animate-fade-up">
          <MessageBubble role="assistant">
            <span className="font-serif italic text-ink-secondary">
              {tourEntryNudge.insight}
            </span>
          </MessageBubble>
        </div>
      )}
    </div>
  );
}

function ParseRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      <span className="text-ink">{value}</span>
    </div>
  );
}
