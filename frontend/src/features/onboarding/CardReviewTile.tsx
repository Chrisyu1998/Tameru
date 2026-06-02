import { useState } from "react";
import { ChevronDown, Pencil, Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Pill } from "@/components/Pill";
import { useCategoryLabel } from "@/lib/categories";
import { cn } from "@/lib/utils";
import type { CardPreview, CardCategoryReward } from "./cardFixtures";

interface CardReviewTileProps {
  preview: CardPreview;
  onSave: () => void;
  onTryAgain: () => void;
  onDiscard: () => void;
}

const confidenceClasses = {
  moss: "bg-moss",
  amber: "bg-warn",
  terracotta: "bg-over",
};

const CONFIDENCE_LABEL_KEYS = {
  moss: "onboarding.cardReview.highConfidence",
  amber: "onboarding.cardReview.mediumConfidence",
  terracotta: "onboarding.cardReview.lowConfidence",
} as const;

export function CardReviewTile({
  preview,
  onSave,
  onTryAgain,
  onDiscard,
}: CardReviewTileProps) {
  const { t } = useTranslation();
  const catLabel = useCategoryLabel();
  const [rows, setRows] = useState<CardCategoryReward[]>(preview.categories);
  const [confirmed, setConfirmed] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ category: string; value: string }>({
    category: "",
    value: "",
  });
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const startEdit = (i: number) => {
    setEditing(i);
    setDraft({ category: rows[i].category, value: rows[i].value });
  };

  const saveEdit = () => {
    if (editing === null) return;
    const next = [...rows];
    next[editing] = { ...next[editing], category: draft.category, value: draft.value };
    setRows(next);
    setConfirmed(new Set([...confirmed, editing]));
    setEditing(null);
  };

  const cancelEdit = () => setEditing(null);

  return (
    <div className="rounded-3xl border border-hairline bg-elevated p-5 animate-fade-up">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-serif text-lg text-ink lowercase-title">{preview.name}</h3>
        <Pill tone="warn">{t("onboarding.cardReview.pillLabel")}</Pill>
      </div>

      <ul className="mt-4 flex flex-col">
        {rows.map((row, i) => {
          const isConfirmed = confirmed.has(i);
          const isEditing = editing === i;

          return (
            <li
              key={i}
              className={cn(
                "flex items-center gap-3 border-b border-hairline py-3 last:border-b-0 transition-colors",
                isConfirmed && "bg-moss-wash/40 -mx-2 rounded-lg px-2"
              )}
            >
              <span
                aria-label={t(CONFIDENCE_LABEL_KEYS[row.confidence])}
                className={cn(
                  "h-2 w-2 shrink-0 rounded-full",
                  confidenceClasses[row.confidence]
                )}
              />

              {isEditing ? (
                <div className="flex flex-1 items-center gap-2">
                  <input
                    value={draft.category}
                    onChange={(e) => setDraft({ ...draft, category: e.target.value })}
                    className="flex-1 min-w-0 rounded-lg border border-hairline bg-canvas px-2 py-1 text-sm text-ink focus:outline-none focus:border-moss"
                  />
                  <input
                    value={draft.value}
                    onChange={(e) => setDraft({ ...draft, value: e.target.value })}
                    className="w-24 rounded-lg border border-hairline bg-canvas px-2 py-1 text-sm tabular text-ink focus:outline-none focus:border-moss"
                  />
                  <button
                    type="button"
                    onClick={saveEdit}
                    aria-label={t("onboarding.cardReview.confirmEdit")}
                    className="flex h-7 w-7 items-center justify-center rounded-full bg-moss text-surface"
                  >
                    <Check className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={cancelEdit}
                    aria-label={t("onboarding.cardReview.cancelEdit")}
                    className="flex h-7 w-7 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : (
                <>
                  <span
                    className={cn(
                      "flex-1 text-[0.92rem]",
                      isConfirmed ? "text-ink" : "italic text-ink-secondary"
                    )}
                  >
                    {row.category}
                  </span>
                  <span
                    className={cn(
                      "tabular text-sm",
                      isConfirmed ? "text-ink font-medium" : "text-ink-secondary italic"
                    )}
                  >
                    {row.value}
                  </span>
                  <button
                    type="button"
                    onClick={() => startEdit(i)}
                    aria-label={t("onboarding.cardReview.editRow", { category: catLabel(row.category) })}
                    className="flex h-7 w-7 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        onClick={() => setSourcesOpen((s) => !s)}
        className="mt-3 flex w-full items-center justify-between rounded-xl px-1 py-2 text-left text-xs text-ink-tertiary transition-colors hover:text-ink-secondary"
        aria-expanded={sourcesOpen}
      >
        <span className="lowercase tracking-wider">{t("onboarding.cardReview.sources")}</span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 transition-transform",
            sourcesOpen && "rotate-180"
          )}
        />
      </button>
      {sourcesOpen && (
        <ul className="mb-1 flex flex-col gap-1.5 px-1 text-xs text-ink-tertiary">
          {preview.sources.map((src) => (
            <li key={src} className="flex items-center gap-2">
              <span className="text-ink-quaternary">·</span>
              {src}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-5 flex flex-col gap-2">
        <Button fullWidth onClick={onSave}>
          {t("onboarding.cardReview.saveCard")}
        </Button>
        <div className="flex items-center justify-between gap-3">
          <Button variant="secondary" size="sm" onClick={onTryAgain} className="flex-1">
            {t("onboarding.cardReview.tryAgain")}
          </Button>
          <button
            type="button"
            onClick={onDiscard}
            className="text-sm text-over hover:underline underline-offset-4 px-3"
          >
            {t("onboarding.cardReview.discard")}
          </button>
        </div>
      </div>
    </div>
  );
}
