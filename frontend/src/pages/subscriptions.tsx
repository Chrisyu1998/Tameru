import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Pause, Play, X } from "lucide-react";
import { SketchIcon } from "@/components/SketchIcon";
import { BottomSheet } from "@/components/BottomSheet";
import { Pill } from "@/components/Pill";
import { useLedger } from "@/lib/ledger";
import {
  cancelSubscription,
  formatFrequency,
  pauseSubscription,
  reassignSubscriptionCard,
  resumeSubscription,
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
  const [selected, setSelected] = useState<SubscriptionRow | null>(null);

  // Surface the needs-new-card banner: any active card-deletion that
  // left subscriptions in `status='paused'` with their backing card now
  // soft-deleted. The cards list (via `useLedger`) is the source of
  // truth for whether the backing card is still active. (DESIGN.md §8.3
  // split-cascade rule.)
  const { cards } = useLedger();
  const cardsById = useMemo(() => {
    const m = new Map<string, (typeof cards)[number]>();
    for (const c of cards) m.set(c.id, c);
    return m;
  }, [cards]);

  const active = items.filter((s) => s.status === "active");
  const paused = items.filter((s) => s.status === "paused");
  const cancelled = items.filter((s) => s.status === "cancelled");
  const needsCard = paused.filter((s) => s.card_id != null && !cardsById.has(s.card_id));

  const askToAdd = () => {
    setChatSeed("Add a new subscription:");
    navigate("/chat");
  };

  const editInChat = (sub: SubscriptionRow) => {
    setChatSeed(`Edit my ${sub.name} subscription:`);
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

      <DetailSheet
        sub={selected}
        onClose={() => setSelected(null)}
        onEditInChat={editInChat}
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

function DetailSheet({
  sub,
  onClose,
  onEditInChat,
}: {
  sub: SubscriptionRow | null;
  onClose: () => void;
  onEditInChat: (s: SubscriptionRow) => void;
}) {
  const { cards } = useLedger();
  if (!sub) {
    return (
      <BottomSheet open={false} onClose={onClose}>
        {null}
      </BottomSheet>
    );
  }
  const card = sub.card_id ? cards.find((c) => c.id === sub.card_id) : null;
  // The §8.3 split-cascade leaves `card_id` pointing at the now-deleted
  // card. The cards page filter strips deleted-and-no-tx-in-scope rows
  // from `useLedger().cards`, so a missing lookup here is the signal
  // that the backing card was closed. If we don't gate the resume
  // button on this, the new server guard 422s with `card_deleted` —
  // matching server behavior in the UI keeps the user out of a
  // confusing dead end.
  const needsNewCard = sub.card_id != null && !card;

  const togglePause = (): void => {
    if (sub.status === "active") void pauseSubscription(sub.id);
    else if (sub.status === "paused") void resumeSubscription(sub.id);
    onClose();
  };
  const resumeAsAch = (): void => {
    // The simplest user-recoverable path when the backing card is
    // gone: drop the dead link, switch to bank ACH, and resume.
    // Reassigning to a different active card is a chat-driven flow
    // (the bottom "ask tameru ai" affordance below).
    void reassignSubscriptionCard(sub.id, null).then(() =>
      resumeSubscription(sub.id),
    );
    onClose();
  };
  const cancel = (): void => {
    void cancelSubscription(sub.id);
    onClose();
  };

  return (
    <BottomSheet open onClose={onClose} ariaLabel="subscription details">
      <header>
        <h2 className="font-serif text-2xl text-ink lowercase-title">
          {sub.name.toLowerCase()}
        </h2>
        {sub.status === "paused" && (
          <Pill tone="neutral" className="mt-2">
            paused
          </Pill>
        )}
        {sub.status === "cancelled" && (
          <Pill tone="neutral" className="mt-2">
            cancelled
          </Pill>
        )}
      </header>

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4">
        <DetailField label="amount" value={formatAmount(sub.amount)} mono />
        <DetailField label="frequency" value={formatFrequency(sub.frequency)} />
        <DetailField
          label={sub.status === "active" ? "next billing" : "last billing"}
          value={formatShortDate(sub.next_billing_date)}
        />
        <DetailField
          label="card"
          value={
            sub.card_id == null
              ? "—  (bank ACH)"
              : card
              ? `${card.name.split(" ")[0]} ···· ${card.last4 ?? ""}`
              : "needs a new card"
          }
        />
        <DetailField label="category" value={sub.category.toLowerCase()} />
        <DetailField label="started" value={formatShortDate(sub.start_date)} />
      </dl>

      {sub.status !== "cancelled" && (
        <div className="mt-7 flex flex-col gap-2">
          {sub.status === "paused" && needsNewCard ? (
            <>
              <p className="rounded-2xl border border-warn-wash/60 bg-warn-wash/20 px-3 py-2 text-[0.78rem] text-ink-secondary">
                this subscription's card was closed. resume as bank ACH,
                or ask tameru to reassign it to another card.
              </p>
              <button
                type="button"
                onClick={resumeAsAch}
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-[0.95rem] text-ink hover:bg-elevated"
              >
                <Play className="h-4 w-4" /> resume as bank ACH
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={togglePause}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-[0.95rem] text-ink hover:bg-elevated"
            >
              {sub.status === "active" ? (
                <>
                  <Pause className="h-4 w-4" /> pause
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" /> resume
                </>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={cancel}
            className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl bg-warn-wash/40 text-[0.95rem] text-over hover:bg-warn-wash/70"
          >
            <X className="h-4 w-4" /> cancel subscription
          </button>
        </div>
      )}

      <p className="mt-5 text-center text-[0.72rem] text-ink-tertiary">
        billing cadence and start date are fixed — cancel and re-add to change
        them.
      </p>

      <button
        type="button"
        onClick={() => onEditInChat(sub)}
        className="mt-5 inline-flex w-full items-center justify-center gap-2 text-[0.82rem] text-ink-secondary hover:text-ink"
      >
        <SketchIcon kind="sparkle" size={14} seed={43} className="text-moss" />
        <span>to edit amount / category / card — ask tameru ai</span>
        <ArrowRight className="h-3.5 w-3.5" />
      </button>
    </BottomSheet>
  );
}

function DetailField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col">
      <dt className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </dt>
      <dd className={cn("mt-0.5 text-[0.95rem] text-ink", mono && "tabular")}>
        {value}
      </dd>
    </div>
  );
}
