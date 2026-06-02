import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Pill } from "@/components/Pill";
import { SwipeableRow } from "@/components/SwipeableRow";
import { SketchIcon } from "@/components/SketchIcon";
import { SketchIllustration } from "@/components/SketchIllustration";
import { PendingDeleteProgress } from "@/components/PendingDeleteProgress";
import { EditCardSheet } from "@/components/EditCardSheet";
import { EditCardAfSheet } from "@/components/EditCardAfSheet";
import { ledger, useLedger } from "@/lib/ledger";
import { setChatSeed } from "@/lib/chatSeed";
import { formatFullDate } from "@/lib/format";
import { ISSUER_LABELS } from "@/lib/cardsApi";
import { listSubscriptions, type SubscriptionRow } from "@/lib/subscriptionsApi";
import { cn } from "@/lib/utils";
import type { Card } from "@/lib/fixtures";

/**
 * Recognition triple for card annual-fee subscriptions (Day 19b,
 * DESIGN.md §6.5). Matches what `insert_card_with_af` /
 * `update_card_af` / `soft_delete_card` write and look for. Kept in
 * the cards page (not subscriptionsApi.ts) because this is the only
 * surface that fetches `include_card_af=true` and needs to filter
 * client-side.
 */
function isCardAfSubscription(sub: SubscriptionRow): boolean {
  return (
    sub.category === "Memberships" &&
    sub.frequency === "annual" &&
    sub.name.endsWith(" annual fee")
  );
}

/**
 * True when the card has a positive annual fee that the user could
 * start tracking. The cards-list tile renders a "track AF" affordance
 * for these when no active AF subscription exists yet — covers users
 * who skipped the renewal date at create time or hit "stop tracking
 * this AF" and want to re-enable.
 */
function hasTrackableFee(card: Card): boolean {
  const fee = parseFloat(card.annualFee ?? "");
  return Number.isFinite(fee) && fee > 0;
}

export default function CardsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { cards, pendingCardDeletes } = useLedger();
  const [editing, setEditing] = useState<Card | null>(null);
  const [editingAf, setEditingAf] = useState<Card | null>(null);
  const [afByCardId, setAfByCardId] = useState<Record<string, SubscriptionRow>>(
    {},
  );

  const refreshAfs = useCallback(async () => {
    try {
      const resp = await listSubscriptions("active", { includeCardAf: true });
      const byCard: Record<string, SubscriptionRow> = {};
      for (const sub of resp.items) {
        if (sub.card_id && isCardAfSubscription(sub)) {
          byCard[sub.card_id] = sub;
        }
      }
      setAfByCardId(byCard);
    } catch {
      // Non-fatal: the chip just won't render. The cards page itself
      // remains usable.
    }
  }, []);

  useEffect(() => {
    void refreshAfs();
  }, [refreshAfs]);

  const askToAddCard = () => {
    setChatSeed(t("cards.chatSeedAddCard"));
    navigate("/chat");
  };

  const requestDelete = (card: Card) => {
    // The row stays visible during the undo window with the moss line
    // sweeping across the bottom — same pattern as transactions in the
    // breakdown list. Tapping the row before the timer fires undoes it.
    setEditing(null);
    ledger.scheduleDeleteCard(card.id);
  };

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-8 pb-24">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">{t("cards.title")}</h1>
        <p className="mt-2 text-sm text-ink-tertiary">
          {t("cards.subtitle")}
        </p>
      </header>

      {cards.length === 0 ? (
        <EmptyCards onAsk={askToAddCard} />
      ) : (
        <ul className="mt-6 flex flex-col gap-3">
          {cards.map((card) => {
            const pending = pendingCardDeletes[card.id];
            const af = afByCardId[card.id] ?? null;
            return (
              <li key={card.id}>
                <SwipeableRow
                  onConfirmDelete={() => requestDelete(card)}
                  onEdit={() => setEditing(card)}
                >
                  <button
                    type="button"
                    onClick={() =>
                      pending
                        ? ledger.undoDeleteCard(card.id)
                        : setEditing(card)
                    }
                    className={cn(
                      "block w-full text-left transition-opacity",
                      pending && "opacity-55",
                    )}
                  >
                    <CardTile
                      card={card}
                      pending={!!pending}
                      af={af}
                      onAfTap={() => setEditingAf(card)}
                    />
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
        </ul>
      )}

      <AIHintFooter
        label={t("cards.hintAddCard")}
        onClick={askToAddCard}
      />

      <EditCardSheet
        open={editing !== null}
        card={editing}
        onClose={() => setEditing(null)}
        onRequestDelete={(card) => {
          setEditing(null);
          ledger.scheduleDeleteCard(card.id);
        }}
      />

      <EditCardAfSheet
        open={editingAf !== null}
        card={editingAf}
        afNextDate={editingAf ? afByCardId[editingAf.id]?.next_billing_date ?? null : null}
        afAmount={editingAf ? afByCardId[editingAf.id]?.amount ?? null : null}
        onClose={() => setEditingAf(null)}
        onSaved={() => {
          void refreshAfs();
        }}
      />
    </div>
  );
}

function CardTile({
  card,
  pending,
  af,
  onAfTap,
}: {
  card: Card;
  pending: boolean;
  af: SubscriptionRow | null;
  onAfTap: () => void;
}) {
  const { t } = useTranslation();
  const stripe = card.color ?? "#8A8377";
  return (
    <div className="relative flex items-stretch overflow-hidden">
      <span
        aria-hidden
        className="w-1.5 flex-shrink-0 rounded-l-2xl"
        style={{ backgroundColor: stripe }}
      />
      <div className="flex-1 px-4 py-3.5">
        <div className="flex items-baseline justify-between gap-3">
          <span
            className={cn(
              "text-[0.95rem] text-ink",
              pending && "line-through decoration-1",
            )}
          >
            {card.name}
          </span>
          <span className="tabular text-[0.78rem] text-ink-tertiary">
            ···· {card.last4}
          </span>
        </div>
        {pending ? (
          <p className="mt-2 text-[0.72rem] text-moss-deep tabular">
            {t("cards.deletingTapToUndo")}
          </p>
        ) : (
          <>
            {(card.issuer ||
              card.program ||
              (card.multipliers && card.multipliers.length > 0)) && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {card.issuer && (
                  <Pill tone="ink">{ISSUER_LABELS[card.issuer]}</Pill>
                )}
                {card.program && <Pill tone="ink">{card.program}</Pill>}
                {card.multipliers?.map((m) => (
                  <Pill key={`${m.label}-${m.factor}`} tone="moss">
                    {m.factor}× {m.label}
                  </Pill>
                ))}
              </div>
            )}
            {af ? (
              <div
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  onAfTap();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    onAfTap();
                  }
                }}
                className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-hairline px-2 py-0.5 text-[0.72rem] text-ink-tertiary hover:bg-elevated cursor-pointer"
                aria-label={t("cards.afChip.editAriaLabel")}
              >
                <RefreshCw className="h-3 w-3" />
                <span className="tabular">${formatAfAmount(af.amount)}</span>
                <span>·</span>
                <span>{t("cards.afChip.next", { date: formatAfDate(af.next_billing_date) })}</span>
              </div>
            ) : (
              hasTrackableFee(card) && (
                <div
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    onAfTap();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      e.stopPropagation();
                      onAfTap();
                    }
                  }}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-dashed border-hairline px-2 py-0.5 text-[0.72rem] text-ink-quaternary hover:bg-elevated cursor-pointer"
                  aria-label={t("cards.afChip.trackAriaLabel")}
                >
                  <RefreshCw className="h-3 w-3" />
                  <span>{t("cards.afChip.trackLabel", { amount: formatAfAmount(card.annualFee ?? "") })}</span>
                </div>
              )
            )}
          </>
        )}
      </div>
    </div>
  );
}

function formatAfAmount(amount: string): string {
  // Trim trailing ".00" so "$550" reads cleaner than "$550.00" on the chip.
  const parsed = parseFloat(amount);
  if (!Number.isFinite(parsed)) return amount;
  return parsed % 1 === 0 ? parsed.toFixed(0) : parsed.toFixed(2);
}

// "2027-03-15" → "Mar 15, 2027" (localized). Wide enough on mobile; the chip
// is a single line. Shared helper keeps the locale logic in one place.
const formatAfDate = formatFullDate;

function EmptyCards({ onAsk }: { onAsk: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="mt-12 flex flex-col items-center text-center">
      <SketchIllustration kind="no-cards" size={108} className="text-ink-tertiary" />
      <p className="mt-4 font-serif text-xl text-ink lowercase-title">
        {t("cards.empty.heading")}
      </p>
      <p className="mt-1 max-w-[28ch] text-[0.85rem] text-ink-tertiary">
        {t("cards.empty.body")}
      </p>
      <button
        type="button"
        onClick={onAsk}
        className="mt-5 inline-flex h-11 items-center gap-2 rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
      >
        <SketchIcon kind="sparkle" size={16} seed={9} />
        {t("cards.empty.cta")}
      </button>
    </div>
  );
}

/** Reusable across /cards and /subscriptions. */
export function AIHintFooter({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <div className="mt-10 border-t border-hairline pt-6">
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-2 text-[0.85rem] text-ink-secondary hover:text-ink"
      >
        <SketchIcon kind="sparkle" size={14} seed={31} className="text-moss" />
        <span>{label}</span>
        <ArrowRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
