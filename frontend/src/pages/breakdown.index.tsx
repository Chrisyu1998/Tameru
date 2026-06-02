import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Donut } from "@/components/Donut";
import {
  currentMonthTransactions,
  ledger,
  totalCents,
  useLedger,
} from "@/lib/ledger";
import {
  CATEGORIES,
  CATEGORY_TINT,
  CATEGORY_SKETCH,
  useCategoryLabel,
  type Category,
} from "@/lib/categories";
import { GOAL_OVERALL_LABEL, type GoalWithSpend } from "@/lib/goalsApi";
import { formatMoney, formatMonth, formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AutoLoggedBadge } from "@/components/AutoLoggedBadge";
import { SketchIcon } from "@/components/SketchIcon";

export default function BreakdownPage() {
  const { t } = useTranslation();
  const { transactions, cards, goals } = useLedger();
  const catLabel = useCategoryLabel();
  useEffect(() => {
    // Swallow refresh failures here — the strip is supplementary UX
    // and an error on /goals shouldn't keep the donut from rendering.
    // The dedicated /goals page surfaces the same error inline.
    ledger.refreshGoals().catch(() => {});
  }, []);
  const monthGoals = useMemo(
    () => goals.filter((g) => g.goal.period === "month"),
    [goals],
  );
  const monthTx = useMemo(() => currentMonthTransactions(transactions), [transactions]);
  const monthTotal = totalCents(monthTx);

  const byCategory = useMemo(() => {
    const map = new Map<Category, typeof monthTx>();
    for (const cat of CATEGORIES) map.set(cat, []);
    for (const t of monthTx) {
      map.get(t.category)!.push(t);
    }
    return Array.from(map.entries())
      .map(([category, txs]) => ({
        category,
        txs,
        cents: txs.reduce((s, t) => s + t.amountCents, 0),
      }))
      .filter((row) => row.cents > 0)
      .sort((a, b) => b.cents - a.cents);
  }, [monthTx]);

  const [expanded, setExpanded] = useState<Category | null>(null);
  const cardLast4 = (id: string) => cards.find((c) => c.id === id)?.last4 ?? "····";

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-6 pb-12 animate-fade-up">
      <header className="flex items-center justify-between">
        <Link
          to="/"
          aria-label={t("breakdown.backAriaLabel")}
          className="flex h-10 w-10 items-center justify-center -ml-2 rounded-full text-ink-secondary transition-colors hover:bg-sunken/60 hover:text-ink"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <h1 className="font-serif text-lg text-ink lowercase-title">{t("breakdown.title")}</h1>
        <div className="w-10" />
      </header>

      <section className="mt-6 flex flex-col items-center">
        <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          {formatMonth().toLowerCase()}
        </p>

        <div className="mt-3">
          <Donut slices={byCategory.map((c) => ({ category: c.category, cents: c.cents }))}>
            <span className="font-serif text-3xl text-ink tabular leading-none">
              {formatMoney(monthTotal)}
            </span>
            <span className="mt-1 text-xs text-ink-tertiary tabular">
              {t("breakdown.transactionCount", { count: monthTx.length })}
            </span>
          </Donut>
        </div>
      </section>

      {monthGoals.length > 0 && (
        <section className="mt-8">
          <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
            {t("breakdown.vsYourBudgets")}
          </p>
          <ul className="mt-3 flex flex-col gap-2">
            {monthGoals.map((g) => (
              <li key={g.goal.id}>
                <GoalProgressBar goal={g} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-8">
        <ul className="flex flex-col gap-1.5">
          {byCategory.map(({ category, txs, cents }) => {
            const isOpen = expanded === category;
            const recent = [...txs]
              .sort((a, b) => b.date.localeCompare(a.date))
              .slice(0, 3);

            return (
              <li key={category}>
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : category)}
                  className={cn(
                    "flex w-full items-center justify-between rounded-2xl border border-hairline bg-surface px-4 py-3 text-left transition-colors",
                    isOpen ? "bg-elevated" : "hover:bg-elevated"
                  )}
                  aria-expanded={isOpen}
                >
                  <div className="flex items-center gap-3">
                    <span
                      className="inline-flex h-7 w-7 items-center justify-center rounded-lg"
                      style={{
                        backgroundColor: `color-mix(in oklab, ${CATEGORY_TINT[category]} 18%, transparent)`,
                        color: CATEGORY_TINT[category],
                      }}
                    >
                      <SketchIcon kind={CATEGORY_SKETCH[category]} size={16} seed={category.length * 7 + 3} />
                    </span>
                    <span className="font-serif text-[0.95rem] text-ink lowercase-title">
                      {catLabel(category).toLowerCase()}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="tabular text-sm text-ink">
                      {formatMoney(cents)}
                    </span>
                    <ChevronDown
                      className={cn(
                        "h-4 w-4 text-ink-tertiary transition-transform",
                        isOpen && "rotate-180"
                      )}
                    />
                  </div>
                </button>

                {isOpen && (
                  <div className="mt-1 mb-2 rounded-2xl border border-hairline bg-elevated px-4 py-3 animate-slide-up-in">
                    <ul className="flex flex-col">
                      {recent.map((t) => (
                        <li
                          key={t.id}
                          className="flex items-center justify-between border-b border-hairline py-2 last:border-b-0"
                        >
                          <div className="flex flex-col leading-tight min-w-0">
                            <div className="flex items-center gap-1.5 min-w-0">
                              <span className="truncate text-sm text-ink">{t.merchant}</span>
                              {t.autoLogged && <AutoLoggedBadge />}
                            </div>
                            <span className="text-[0.7rem] tabular text-ink-tertiary">
                              {formatShortDate(t.date)} · ···· {cardLast4(t.cardId)}
                            </span>
                          </div>
                          <span className="tabular text-sm text-ink shrink-0 ml-3">
                            {formatMoney(t.amountCents)}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <Link
                      to={`/breakdown/${category.toLowerCase()}`}
                      className="mt-3 inline-flex items-center gap-1 text-xs text-moss hover:text-moss-deep"
                    >
                      {t("breakdown.seeAll", { count: txs.length })}
                    </Link>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

/**
 * Renders one row in the breakdown's goals strip: label + spent/budget +
 * a tinted progress bar. Overall-budget goals (category=null) show "all
 * spending" and use a neutral tint; category goals use the same
 * CATEGORY_TINT as the donut slice for at-a-glance correlation.
 */
function GoalProgressBar({ goal }: { goal: GoalWithSpend }) {
  const catLabel = useCategoryLabel();
  const amount = parseFloat(goal.goal.amount);
  const spent = parseFloat(goal.spent_period_to_date);
  const fillPct = Math.min(100, Math.max(0, goal.progress_ratio * 100));
  const over = goal.progress_ratio >= 1;
  const warn = goal.progress_ratio >= 0.8 && !over;
  const isOverall = goal.goal.category === null;
  const tint = isOverall
    ? "var(--color-ink)"
    : CATEGORY_TINT[goal.goal.category as Category] ?? "var(--color-moss)";
  const barColor = over
    ? "var(--color-over)"
    : warn
      ? "var(--color-warn)"
      : tint;

  return (
    <div className="rounded-2xl border border-hairline bg-surface px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-serif text-[0.95rem] text-ink lowercase-title">
          {isOverall
            ? GOAL_OVERALL_LABEL
            : catLabel(goal.goal.category as Category).toLowerCase()}
        </span>
        <span className="tabular text-[0.78rem] text-ink-tertiary">
          ${spent.toFixed(spent % 1 === 0 ? 0 : 2)} of $
          {amount.toFixed(amount % 1 === 0 ? 0 : 2)}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-sunken">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${fillPct}%`, backgroundColor: barColor }}
        />
      </div>
    </div>
  );
}
