import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EditSubscriptionSheet } from "@/components/EditSubscriptionSheet";
import { useLedger } from "@/lib/ledger";
import {
  formatFrequency,
  useSubscriptions,
  type SubscriptionRow,
} from "@/lib/subscriptions";
import { setChatSeed } from "@/lib/chatSeed";
import { formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AIHintFooter } from "@/pages/cards";

/**
 * Format a wire-side decimal-string amount as USD. The backend stores
 * monetary values as Postgres `numeric` (string round-trip); the UI's
 * `formatMoney` helper takes cents, so we convert here.
 */
function formatAmount(amount: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value)) return amount;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);
}

export default function SubscriptionsPage() {
  const navigate = useNavigate();
  const { items } = useSubscriptions();
  const { cards } = useLedger();
  const [selected, setSelected] = useState<SubscriptionRow | null>(null);

  // Surface the needs-new-card banner: any active card-deletion that
  // left subscriptions in `status='paused'` with their backing card now
  // soft-deleted. The cards list (via `useLedger`) is the source of
  // truth for whether the backing card is still active. (DESIGN.md §8.3
  // split-cascade rule.)
  const cardsById = useMemo(() => {
    const m = new Map<string, (typeof cards)[number]>();
    for (const c of cards) m.set(c.id, c);
    return m;
  }, [cards]);

  const active = items.filter((s) => s.status === "active");
  const paused = items.filter((s) => s.status === "paused");
  const cancelled = items.filter((s) => s.status === "cancelled");
  const needsCard = paused.filter((s) => s.card_id != null && !cardsById.has(s.card_id));

  // Keep the open sheet in sync with the in-memory row so edits land
  // visibly (e.g. amount change applies, save, re-render keeps the
  // updated row visible if the sheet stays open for a follow-up).
  const selectedLive =
    selected !== null ? (items.find((s) => s.id === selected.id) ?? selected) : null;

  const askToAdd = () => {
    setChatSeed("Add a new subscription:");
    navigate("/chat");
  };

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-8 pb-24">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          subscriptions
        </h1>
        <p className="mt-2 text-sm text-ink-tertiary">
          recurring charges, quietly tracked.
        </p>
      </header>

      {needsCard.length > 0 && (
        <div className="mt-5 rounded-2xl border border-warn-wash/60 bg-warn-wash/20 px-4 py-3 text-[0.82rem] text-ink">
          <p className="font-medium">needs a new card</p>
          <p className="mt-1 text-ink-secondary">
            you deleted a card that backed {needsCard.length} subscription
            {needsCard.length === 1 ? "" : "s"}. tap a row below to pick a new
            card, or cancel it.
          </p>
        </div>
      )}

      {items.length === 0 ? (
        <p className="mt-10 text-center text-sm text-ink-tertiary">
          no subscriptions tracked yet — ask tameru to add one.
        </p>
      ) : (
        <>
          {active.length > 0 && (
            <ul className="mt-6 flex flex-col">
              {active.map((sub) => (
                <Row key={sub.id} sub={sub} onSelect={() => setSelected(sub)} />
              ))}
            </ul>
          )}

          {paused.length > 0 && (
            <>
              <SectionHeader label="paused" />
              <ul className="mt-2 flex flex-col">
                {paused.map((sub) => (
                  <Row key={sub.id} sub={sub} onSelect={() => setSelected(sub)} />
                ))}
              </ul>
            </>
          )}

          {cancelled.length > 0 && (
            <>
              <SectionHeader label="cancelled" />
              <ul className="mt-2 flex flex-col">
                {cancelled.map((sub) => (
                  <Row key={sub.id} sub={sub} onSelect={() => setSelected(sub)} />
                ))}
              </ul>
            </>
          )}
        </>
      )}

      <AIHintFooter
        label="ask tameru to add a subscription"
        onClick={askToAdd}
      />

      <EditSubscriptionSheet
        open={selected !== null}
        subscription={selectedLive}
        cards={cards}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="mt-8 flex items-center gap-3">
      <span className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      <span className="h-px flex-1 bg-hairline" />
    </div>
  );
}

function Row({
  sub,
  onSelect,
}: {
  sub: SubscriptionRow;
  onSelect: () => void;
}) {
  const dimmed = sub.status !== "active";
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "group flex w-full items-center justify-between gap-3 border-b border-hairline px-1 py-3.5 text-left transition-opacity hover:bg-elevated/50",
          dimmed && "opacity-55"
        )}
      >
        <div className="min-w-0 flex-1">
          <span className="truncate text-[0.95rem] text-ink">{sub.name}</span>
          {sub.status === "paused" ? (
            <p className="mt-0.5 text-[0.75rem] text-ink-tertiary">
              paused · no upcoming charges
            </p>
          ) : sub.status === "cancelled" ? (
            <p className="mt-0.5 text-[0.75rem] text-ink-tertiary">cancelled</p>
          ) : (
            <p className="mt-0.5 text-[0.75rem] text-ink-tertiary">
              next {formatShortDate(sub.next_billing_date)} ·{" "}
              {formatFrequency(sub.frequency)}
            </p>
          )}
        </div>
        <span className="tabular text-[0.95rem] text-ink">
          {formatAmount(sub.amount)}
        </span>
      </button>
    </li>
  );
}

