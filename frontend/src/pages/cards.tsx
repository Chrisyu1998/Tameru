import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Pill } from "@/components/Pill";
import { SwipeableRow } from "@/components/SwipeableRow";
import { SketchIcon } from "@/components/SketchIcon";
import { SketchIllustration } from "@/components/SketchIllustration";
import { UndoToast, type PendingDelete } from "@/components/UndoToast";
import { ledger, useLedger } from "@/lib/ledger";
import { setChatSeed } from "@/lib/chatSeed";
import type { Card } from "@/lib/fixtures";

export default function CardsPage() {
  const navigate = useNavigate();
  const { cards } = useLedger();
  const [pending, setPending] = useState<PendingDelete | null>(null);
  // Stash so undo can restore the card at its original index.
  const lastRemovedRef = useRef<{ card: Card; index: number } | null>(null);

  const askToAddCard = () => {
    setChatSeed("Add a new card:");
    navigate("/chat");
  };

  const requestDelete = (card: Card) => {
    const index = cards.findIndex((c) => c.id === card.id);
    if (index === -1) return;
    lastRemovedRef.current = { card, index };
    ledger.deleteCard(card.id);
    setPending({
      id: card.id,
      label: `${card.name} ···· ${card.last4}`,
      // The deletion has already happened; commit clears the stash.
      commit: () => {
        lastRemovedRef.current = null;
      },
    });
  };

  const undoDelete = () => {
    const stash = lastRemovedRef.current;
    if (stash) {
      ledger.insertCard(stash.card, stash.index);
      lastRemovedRef.current = null;
    }
    setPending(null);
  };

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-8 pb-24">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">my cards</h1>
        <p className="mt-2 text-sm text-ink-tertiary">
          swipe a card left to remove it.
        </p>
      </header>

      {cards.length === 0 ? (
        <EmptyCards onAsk={askToAddCard} />
      ) : (
        <ul className="mt-6 flex flex-col gap-3">
          {cards.map((card) => (
            <li key={card.id}>
              <SwipeableRow onConfirmDelete={() => requestDelete(card)}>
                <CardTile card={card} />
              </SwipeableRow>
            </li>
          ))}
        </ul>
      )}

      <AIHintFooter
        label="ask tameru to add a card"
        onClick={askToAddCard}
      />

      <UndoToast
        pending={pending}
        onUndo={undoDelete}
        onTimeout={() => setPending(null)}
      />
    </div>
  );
}

function CardTile({ card }: { card: Card }) {
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
          <span className="text-[0.95rem] text-ink">{card.name}</span>
          <span className="tabular text-[0.78rem] text-ink-tertiary">
            ···· {card.last4}
          </span>
        </div>
        {(card.program || (card.multipliers && card.multipliers.length > 0)) && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {card.program && <Pill tone="ink">{card.program}</Pill>}
            {card.multipliers?.map((m) => (
              <Pill key={`${m.label}-${m.factor}`} tone="moss">
                {m.factor}× {m.label}
              </Pill>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyCards({ onAsk }: { onAsk: () => void }) {
  return (
    <div className="mt-12 flex flex-col items-center text-center">
      <SketchIllustration kind="no-cards" size={108} className="text-ink-tertiary" />
      <p className="mt-4 font-serif text-xl text-ink lowercase-title">
        no cards yet
      </p>
      <p className="mt-1 max-w-[28ch] text-[0.85rem] text-ink-tertiary">
        tameru learns better with a card or two on file.
      </p>
      <button
        type="button"
        onClick={onAsk}
        className="mt-5 inline-flex h-11 items-center gap-2 rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
      >
        <SketchIcon kind="sparkle" size={16} seed={9} />
        ask tameru ai to add one
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
