import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { WeeklyRecap } from "@/lib/chatApi";
import { useCategoryLabel } from "@/lib/categories";
import { formatCurrencyAmount, formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Weekly recap card — pinned "this week" summary at the top of the chat
 * screen (DESIGN.md §6.2 / §6.4). Added 2026-07-03.
 *
 * This is the *durable weekly artifact* that relaxes the entry-moment
 * insight's "ephemeral by design" posture: it persists (server-side in
 * `weekly_recap`, re-fetched on every chat open) and re-renders, whereas the
 * per-transaction entry bubbles stay ephemeral. It is NOT a `chat_messages`
 * row — it renders above the append-only thread, so it never pollutes chat
 * history.
 *
 * Content: the same weekly-digest aggregates + Sonnet narrative the email
 * digest uses (`observation`/`nudge` arrive already localized). The headline
 * delta and top-category line are colored on the §6.3 baseline scale
 * (moss = under, amber = at, terracotta = over). Money fields arrive as
 * decimal strings in `home_currency` major units.
 *
 * Collapse: after the user hides it, the collapsed one-line pill persists for
 * that week via `localStorage` keyed on `dedup_week` (the recipient's local
 * Monday) — no backend "seen" state in v1.
 */
export function WeeklyRecapCard({ recap }: { recap: WeeklyRecap }) {
  const { t } = useTranslation();
  const catLabel = useCategoryLabel();
  const [collapsed, setCollapsed] = useState(() =>
    readCollapsed(recap.dedup_week),
  );

  const currency = recap.home_currency;
  const weekTotal = Number(recap.week_total);
  const baseline = Number(recap.baseline_avg);
  const delta = weekTotal - baseline;
  const money = (n: number) => formatCurrencyAmount(Math.abs(n), currency);

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      writeCollapsed(recap.dedup_week, next);
      return next;
    });

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={toggle}
        aria-label={t("chat.recap.expand")}
        className="flex w-full items-center justify-between rounded-xl border border-hairline bg-surface px-4 py-2 text-left transition-colors hover:bg-elevated"
      >
        <span className="flex items-center gap-2">
          <ChevronRight className="h-3.5 w-3.5 text-ink-tertiary" />
          <span className="font-serif text-[0.9rem] text-ink lowercase-title">
            {t("chat.recap.title")}
          </span>
        </span>
        <span className="tabular text-[0.85rem] font-medium text-ink">
          {money(weekTotal)}
        </span>
      </button>
    );
  }

  const deltaLine = _deltaLine(t, baseline, delta, money);
  const topLine = _topLine(recap, t, catLabel, money);

  return (
    <section
      aria-label={t("chat.recap.title")}
      className="animate-slide-up-in rounded-2xl border border-hairline bg-surface px-4 py-3.5"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <button
            type="button"
            onClick={toggle}
            aria-label={t("chat.recap.collapse")}
            className="flex items-center gap-1.5 text-ink"
          >
            <ChevronDown className="h-4 w-4 text-ink-tertiary" />
            <span className="font-serif text-[1.05rem] lowercase-title">
              {t("chat.recap.title")}
            </span>
          </button>
          <p className="mt-0.5 pl-6 text-[0.72rem] text-ink-tertiary">
            {formatShortDate(recap.week_start)}–{formatShortDate(recap.week_end)}
          </p>
        </div>
        <span className="tabular font-serif text-xl leading-none text-ink">
          {money(weekTotal)}
        </span>
      </div>

      <p className={cn("mt-2 text-[0.85rem] font-medium", deltaLine.cls)}>
        {deltaLine.copy}
      </p>

      {topLine && (
        <p className={cn("mt-1.5 text-[0.85rem]", topLine.cls)}>{topLine.copy}</p>
      )}

      <p className="mt-3 text-[0.9rem] leading-relaxed text-ink-secondary">
        {recap.observation}
      </p>
      {recap.nudge && (
        <p className="mt-1.5 text-[0.9rem] leading-relaxed text-ink-tertiary">
          {recap.nudge}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

type Line = { copy: string; cls: string };
type TFn = ReturnType<typeof useTranslation>["t"];

/** Headline total-vs-baseline line, colored on the §6.3 scale. */
function _deltaLine(
  t: TFn,
  baseline: number,
  delta: number,
  money: (n: number) => string,
): Line {
  if (baseline === 0) {
    return { copy: t("chat.recap.baselineStart"), cls: "text-ink-tertiary" };
  }
  if (delta > 0) {
    return { copy: t("chat.recap.above", { delta: money(delta) }), cls: "text-over" };
  }
  if (delta < 0) {
    return {
      copy: t("chat.recap.below", { delta: money(delta) }),
      cls: "text-moss-deep",
    };
  }
  return { copy: t("chat.recap.inline"), cls: "text-warn" };
}

/** Top-category line ("Dining led at $180 · $20 above usual"), or null. */
function _topLine(
  recap: WeeklyRecap,
  t: TFn,
  catLabel: (category: string) => string,
  money: (n: number) => string,
): Line | null {
  if (!recap.top_category || recap.top_category_total == null) return null;
  const cat = catLabel(recap.top_category);
  const amount = money(Number(recap.top_category_total));
  const catBaseline =
    recap.top_category_baseline != null
      ? Number(recap.top_category_baseline)
      : 0;
  const catDelta = Number(recap.top_category_total) - catBaseline;
  if (catDelta > 0) {
    return {
      copy: t("chat.recap.topAbove", { cat, amount, delta: money(catDelta) }),
      cls: "text-over",
    };
  }
  if (catDelta < 0) {
    return {
      copy: t("chat.recap.topBelow", { cat, amount, delta: money(catDelta) }),
      cls: "text-moss-deep",
    };
  }
  return { copy: t("chat.recap.topInline", { cat, amount }), cls: "text-ink-secondary" };
}

const _collapsedKey = (week: string) => `tameru-recap-collapsed-${week}`;

/** Read the per-week collapsed flag (private-mode safe). */
function readCollapsed(week: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(_collapsedKey(week)) === "1";
  } catch {
    return false;
  }
}

/** Persist the per-week collapsed flag (private-mode safe). */
function writeCollapsed(week: string, collapsed: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (collapsed) window.localStorage.setItem(_collapsedKey(week), "1");
    else window.localStorage.removeItem(_collapsedKey(week));
  } catch {
    // ignore — localStorage can be unavailable in private mode
  }
}
