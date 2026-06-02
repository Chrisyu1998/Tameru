import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AutoLoggedBadge } from "@/components/AutoLoggedBadge";
import { cardLabel } from "@/lib/chat";
import { type Card, type Transaction } from "@/lib/fixtures";
import { formatMoney, formatShortDate } from "@/lib/format";

interface CandidateCardsProps {
  preface: string;
  candidates: Transaction[];
  /** Live cards from `useLedger()` — used to resolve each candidate's cardId. */
  cards: Card[];
  /** Caller decides what selection means (open edit sheet / confirm delete). */
  onSelect: (tx: Transaction) => void;
}

const COLLAPSED_VISIBLE = 5;

export function CandidateCards({
  preface,
  candidates,
  cards,
  onSelect,
}: CandidateCardsProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const visible = expanded
    ? candidates
    : candidates.slice(0, COLLAPSED_VISIBLE);
  const hiddenCount = candidates.length - COLLAPSED_VISIBLE;

  return (
    <div className="w-full max-w-[88%] animate-slide-up-in">
      <p className="mb-2 px-1 text-[0.95rem] leading-relaxed text-ink">
        {preface}
      </p>

      {candidates.length === 0 ? (
        <p className="px-1 text-[0.85rem] italic text-ink-tertiary">
          {t("chat.candidateCards.nothingMatched")}
        </p>
      ) : (
        <ul className="overflow-hidden rounded-2xl border border-hairline bg-surface divide-y divide-hairline">
          {visible.map((tx) => {
            const card = cardLabel(tx.cardId, cards);
            return (
              <li key={tx.id}>
                <button
                  type="button"
                  onClick={() => onSelect(tx)}
                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-elevated"
                >
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate text-[0.95rem] text-ink inline-flex items-center gap-1.5">
                      <span className="truncate">{tx.merchant}</span>
                      {tx.autoLogged && <AutoLoggedBadge />}
                    </span>
                    <span className="text-[0.72rem] tabular text-ink-tertiary">
                      {formatShortDate(tx.date)} · ···· {card.last4}
                    </span>
                  </div>
                  <span className="font-serif text-[1rem] tabular text-ink">
                    {formatMoney(tx.amountCents)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {!expanded && hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2 inline-flex items-center rounded-full border border-hairline bg-surface px-3 py-1 text-[0.72rem] text-ink-secondary transition-colors hover:bg-elevated"
        >
          {t("chat.candidateCards.more", { count: hiddenCount })}
        </button>
      )}
    </div>
  );
}
