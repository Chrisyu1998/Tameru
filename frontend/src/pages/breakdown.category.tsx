import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { AutoLoggedBadge } from "@/components/AutoLoggedBadge";
import { SwipeableRow } from "@/components/SwipeableRow";
import { EditTransactionSheet } from "@/components/EditTransactionSheet";
import { PendingDeleteProgress } from "@/components/PendingDeleteProgress";
import { SketchIllustration } from "@/components/SketchIllustration";
import { ledger, useLedger } from "@/lib/ledger";
import { CATEGORIES, CATEGORY_TINT, useCategoryLabel, type Category } from "@/lib/categories";
import { formatMoney, formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Transaction } from "@/lib/fixtures";

type MonthFilter = "all" | "current" | "previous";

function NotFoundPanel({ slug }: { slug: string }) {
  const { t } = useTranslation();
  return (
    <div className="mx-auto w-full max-w-md px-5 pt-16 text-center">
      <h1 className="font-serif text-2xl text-ink lowercase-title">
        {t("breakdown.unknownCategory")}
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        {t("breakdown.unknownCategoryBody", { slug })}
      </p>
      <Link
        to="/breakdown"
        className="mt-6 inline-block text-sm text-moss hover:text-moss-deep"
      >
        {t("breakdown.backToBreakdown")}
      </Link>
    </div>
  );
}

export default function CategoryListPage() {
  const params = useParams<{ category: string }>();
  const slug = (params.category ?? "").toLowerCase();
  const category = CATEGORIES.find((c) => c.toLowerCase() === slug) as
    | Category
    | undefined;
  if (!category) return <NotFoundPanel slug={params.category ?? ""} />;
  return <CategoryListBody category={category} />;
}

function CategoryListBody({ category }: { category: Category }) {
  const { t } = useTranslation();
  const { transactions, cards, pendingDeletes } = useLedger();
  const catLabel = useCategoryLabel();

  const [monthFilter, setMonthFilter] = useState<MonthFilter>("all");
  const [cardFilter, setCardFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Transaction | null>(null);

  const filtered = useMemo(() => {
    const now = new Date();
    return transactions
      .filter((t) => t.category === category)
      .filter((t) => {
        if (monthFilter === "all") return true;
        const d = new Date(t.date + "T00:00:00");
        const sameMonth = (offset: number) => {
          const ref = new Date(now.getFullYear(), now.getMonth() - offset, 1);
          return (
            d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth()
          );
        };
        return monthFilter === "current" ? sameMonth(0) : sameMonth(1);
      })
      .filter((t) => (cardFilter === "all" ? true : t.cardId === cardFilter))
      .filter((t) =>
        search.trim().length === 0
          ? true
          : t.merchant.toLowerCase().includes(search.trim().toLowerCase())
      )
      .sort((a, b) => b.date.localeCompare(a.date));
  }, [transactions, category, monthFilter, cardFilter, search]);

  const total = filtered.reduce((s, t) => s + t.amountCents, 0);

  const requestDelete = (tx: Transaction) => {
    // The row stays in the list with a moss progress bar; tapping it
    // during the window calls ledger.undoDelete. The timer lives at
    // module scope, so navigating away no longer cancels the commit.
    setEditing(null);
    ledger.scheduleDelete(tx.id);
  };

  const cardLast4 = (id: string) => cards.find((c) => c.id === id)?.last4 ?? "····";

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-6 pb-24 animate-fade-up">
      <header className="flex items-center justify-between">
        <Link
          to="/breakdown"
          aria-label={t("breakdown.backAriaLabel")}
          className="flex h-10 w-10 items-center justify-center -ml-2 rounded-full text-ink-secondary transition-colors hover:bg-sunken/60 hover:text-ink"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="flex items-center gap-2">
          <span
            className="h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: CATEGORY_TINT[category as Category] }}
          />
          <h1 className="font-serif text-lg text-ink lowercase-title">
            {catLabel(category).toLowerCase()}
          </h1>
        </div>
        <div className="w-10" />
      </header>

      <p className="mt-4 text-center text-[0.7rem] uppercase tracking-wider text-ink-tertiary tabular">
        {formatMoney(total)} · {t("breakdown.transactionCount", { count: filtered.length })}
      </p>

      {/* Filter chips */}
      <div className="mt-5 flex flex-col gap-3">
        <ChipRow label={t("breakdown.filterMonth")}>
          <Chip active={monthFilter === "all"} onClick={() => setMonthFilter("all")}>
            {t("breakdown.filterAll")}
          </Chip>
          <Chip active={monthFilter === "current"} onClick={() => setMonthFilter("current")}>
            {t("breakdown.filterThisMonth")}
          </Chip>
          <Chip active={monthFilter === "previous"} onClick={() => setMonthFilter("previous")}>
            {t("breakdown.filterLastMonth")}
          </Chip>
        </ChipRow>

        <ChipRow label={t("breakdown.filterCard")}>
          <Chip active={cardFilter === "all"} onClick={() => setCardFilter("all")}>
            {t("breakdown.filterAllCards")}
          </Chip>
          {cards.map((c) => (
            <Chip
              key={c.id}
              active={cardFilter === c.id}
              onClick={() => setCardFilter(c.id)}
            >
              ···· {c.last4}
            </Chip>
          ))}
        </ChipRow>

        <div className="flex items-center gap-3 rounded-2xl border border-hairline bg-elevated px-4 py-2.5">
          <Search className="h-4 w-4 text-ink-tertiary" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("breakdown.searchPlaceholder")}
            className="flex-1 bg-transparent text-sm text-ink placeholder:text-ink-quaternary focus:outline-none"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label={t("breakdown.clearSearch")}
              className="text-ink-tertiary hover:text-ink"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Transaction list */}
      <ul className="mt-6 flex flex-col gap-1.5">
        {filtered.map((tx) => {
          const pending = pendingDeletes[tx.id];
          return (
            <li key={tx.id}>
              <SwipeableRow
                onConfirmDelete={() => requestDelete(tx)}
                onEdit={() => setEditing(tx)}
              >
                <button
                  type="button"
                  onClick={() =>
                    pending ? ledger.undoDelete(tx.id) : setEditing(tx)
                  }
                  className={cn(
                    "flex w-full items-center justify-between bg-surface px-4 py-3 text-left transition-colors",
                    pending ? "opacity-55" : "hover:bg-elevated"
                  )}
                >
                  <div className="flex flex-col leading-tight min-w-0">
                    <span
                      className={cn(
                        "truncate text-[0.95rem] text-ink inline-flex items-center gap-1.5",
                        pending && "line-through decoration-1"
                      )}
                    >
                      <span className="truncate">{tx.merchant}</span>
                      {tx.autoLogged && <AutoLoggedBadge />}
                    </span>
                    <span
                      className={cn(
                        "text-[0.72rem] tabular",
                        pending ? "text-moss-deep" : "text-ink-tertiary"
                      )}
                    >
                      {pending
                        ? t("breakdown.deletingTapToUndo")
                        : `${formatShortDate(tx.date)} · ···· ${cardLast4(
                            tx.cardId
                          )}`}
                    </span>
                  </div>
                  <span
                    className={cn(
                      "tabular text-sm shrink-0 ml-3",
                      pending ? "text-ink-tertiary" : "text-ink"
                    )}
                  >
                    {formatMoney(tx.amountCents)}
                  </span>
                </button>
                {pending && (
                  <PendingDeleteProgress
                    scheduledAt={pending.scheduledAt}
                    durationMs={pending.durationMs}
                  />
                )}
              </SwipeableRow>
            </li>
          );
        })}
        {filtered.length === 0 && (
          <li className="flex flex-col items-center gap-3 rounded-2xl border border-hairline bg-sunken/40 py-10 text-center text-sm text-ink-tertiary">
            <SketchIllustration kind="empty-list" size={84} className="text-ink-tertiary/70" />
            <span>{t("breakdown.emptyFilters")}</span>
          </li>
        )}
      </ul>

      <EditTransactionSheet
        open={editing !== null}
        transaction={editing}
        cards={cards}
        onClose={() => setEditing(null)}
        onRequestDelete={requestDelete}
      />
    </div>
  );
}

function ChipRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-[0.65rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-xs transition-colors",
        active
          ? "border-moss bg-moss-wash text-moss-deep"
          : "border-hairline bg-surface text-ink-secondary hover:bg-sunken/60"
      )}
    >
      {children}
    </button>
  );
}
