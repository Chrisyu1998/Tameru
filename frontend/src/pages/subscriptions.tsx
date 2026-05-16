import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Pause, Play, X } from "lucide-react";
import { SketchIcon } from "@/components/SketchIcon";
import { BottomSheet } from "@/components/BottomSheet";
import { Pill } from "@/components/Pill";
import { useLedger } from "@/lib/ledger";
import {
  formatFrequency,
  subscriptions,
  useSubscriptions,
  type Subscription,
} from "@/lib/subscriptions";
import { setChatSeed } from "@/lib/chatSeed";
import { formatMoney, formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AIHintFooter } from "@/pages/cards";

export default function SubscriptionsPage() {
  const navigate = useNavigate();
  const subs = useSubscriptions();
  const [selected, setSelected] = useState<Subscription | null>(null);

  const active = subs.filter((s) => s.status === "active");
  const inactive = subs.filter((s) => s.status === "paused");

  const askToAdd = () => {
    setChatSeed("Add a new subscription:");
    navigate("/chat");
  };

  const editInChat = (sub: Subscription) => {
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

      {active.length === 0 && inactive.length === 0 ? (
        <p className="mt-10 text-center text-sm text-ink-tertiary">
          no subscriptions tracked yet — ask tameru to add one.
        </p>
      ) : (
        <ul className="mt-6 flex flex-col">
          {active.map((sub) => (
            <SubscriptionRow
              key={sub.id}
              sub={sub}
              onSelect={() => setSelected(sub)}
            />
          ))}
        </ul>
      )}

      {/* Inactive */}
      {inactive.length > 0 && (
        <>
          <div className="mt-8 flex items-center gap-3">
            <span className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
              inactive
            </span>
            <span className="h-px flex-1 bg-hairline" />
          </div>
          <ul className="mt-2 flex flex-col">
            {inactive.map((sub) => (
              <SubscriptionRow
                key={sub.id}
                sub={sub}
                onSelect={() => setSelected(sub)}
              />
            ))}
          </ul>
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

function SubscriptionRow({
  sub,
  onSelect,
}: {
  sub: Subscription;
  onSelect: () => void;
}) {
  const paused = sub.status === "paused";
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "group flex w-full items-center justify-between gap-3 border-b border-hairline px-1 py-3.5 text-left transition-opacity hover:bg-elevated/50",
          paused && "opacity-55"
        )}
      >
        <div className="min-w-0 flex-1">
          <span className="truncate text-[0.95rem] text-ink">{sub.name}</span>
          {paused ? (
            <p className="mt-0.5 text-[0.75rem] text-ink-tertiary">
              paused · no upcoming charges
            </p>
          ) : (
            <p className="mt-0.5 text-[0.75rem] text-ink-tertiary">
              next {formatShortDate(sub.nextBilling)} · {formatFrequency(sub.frequency)}
            </p>
          )}
        </div>
        <span className="tabular text-[0.95rem] text-ink">
          {formatMoney(sub.amountCents)}
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
  sub: Subscription | null;
  onClose: () => void;
  onEditInChat: (s: Subscription) => void;
}) {
  const { cards } = useLedger();
  if (!sub) {
    return (
      <BottomSheet open={false} onClose={onClose}>
        {null}
      </BottomSheet>
    );
  }
  const card = cards.find((c) => c.id === sub.cardId);

  const togglePause = () => {
    if (sub.status === "active") subscriptions.pause(sub.id);
    else subscriptions.resume(sub.id);
    onClose();
  };
  const cancel = () => {
    subscriptions.cancel(sub.id);
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
      </header>

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4">
        <DetailField label="amount" value={formatMoney(sub.amountCents)} mono />
        <DetailField label="frequency" value={formatFrequency(sub.frequency)} />
        <DetailField
          label={sub.status === "active" ? "next billing" : "last billing"}
          value={formatShortDate(sub.nextBilling)}
        />
        <DetailField
          label="card"
          value={card ? `${card.name.split(" ")[0]} ···· ${card.last4}` : "—"}
        />
        <DetailField label="category" value={sub.category.toLowerCase()} />
        <DetailField label="started" value={formatShortDate(sub.startedOn)} />
      </dl>

      <div className="mt-7 flex flex-col gap-2">
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
        <button
          type="button"
          onClick={cancel}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl bg-warn-wash/40 text-[0.95rem] text-over hover:bg-warn-wash/70"
        >
          <X className="h-4 w-4" /> cancel subscription
        </button>
      </div>

      <button
        type="button"
        onClick={() => onEditInChat(sub)}
        className="mt-5 inline-flex w-full items-center justify-center gap-2 text-[0.82rem] text-ink-secondary hover:text-ink"
      >
        <SketchIcon kind="sparkle" size={14} seed={43} className="text-moss" />
        <span>to edit details — ask tameru ai</span>
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
