import { Link, useNavigate } from "react-router-dom";
import { ArrowUpRight, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { DeltaTile } from "@/components/DeltaTile";
import {
  currentMonthTransactions,
  dismissFirstHint,
  isFirstHintDismissed,
  totalCents,
  useLedger,
} from "@/lib/ledger";
import {
  CATEGORY_BASELINES,
  TOTAL_BASELINE,
} from "@/lib/fixtures";
import { CATEGORIES, type Category } from "@/lib/categories";
import { formatMoney, formatMonth, formatPercent } from "@/lib/format";
import { useAppStore } from "@/store";
import { cn } from "@/lib/utils";

const PREFILL_CHIPS = ["coffee $5.50", "lunch with M $24"];

export default function HomePage() {
  const navigate = useNavigate();
  const jwt = useAppStore((s) => s.jwt);
  const homeCurrency = useAppStore((s) => s.homeCurrency);
  const { transactions } = useLedger();
  const [hintDismissed, setHintDismissed] = useState(true);

  // Gate: redirect anyone who isn't fully onboarded to the wizard. We check
  // for missing JWT (signed out) or missing home_currency (signed in but
  // hasn't completed /auth/bootstrap yet). homeCurrency=undefined means /me
  // hasn't resolved yet — hold render until it does to avoid a flicker.
  const onboarded = !!jwt && typeof homeCurrency === "string";
  const shouldGate = !jwt || homeCurrency === null;

  useEffect(() => {
    if (shouldGate) {
      navigate("/onboarding", { replace: true });
      return;
    }
    setHintDismissed(isFirstHintDismissed());
  }, [navigate, shouldGate]);

  if (!onboarded) return null;

  const monthTx = currentMonthTransactions(transactions);
  const isEmpty = monthTx.length === 0;

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-10 pb-12 animate-fade-up">
      {isEmpty ? (
        <EmptyHome
          showHint={!hintDismissed}
          onDismissHint={() => {
            dismissFirstHint();
            setHintDismissed(true);
          }}
        />
      ) : (
        <PopulatedHome monthTx={monthTx} />
      )}
    </div>
  );
}

/* ─── Populated ──────────────────────────────────────────────── */

function PopulatedHome({ monthTx }: { monthTx: ReturnType<typeof currentMonthTransactions> }) {
  const monthTotal = totalCents(monthTx);
  const deltaPct = ((monthTotal - TOTAL_BASELINE) / TOTAL_BASELINE) * 100;

  // Pick the four most movement-y categories: largest absolute delta from baseline,
  // among categories that have any spend this month.
  const tilesData = CATEGORIES.map((cat) => {
    const catSpend = monthTx
      .filter((t) => t.category === cat)
      .reduce((s, t) => s + t.amountCents, 0);
    const baseline = CATEGORY_BASELINES[cat] ?? 0;
    const deltaCents = catSpend - baseline;
    return { category: cat as Category, catSpend, baseline, deltaCents };
  })
    .filter((d) => d.catSpend > 0)
    .sort((a, b) => Math.abs(b.deltaCents) - Math.abs(a.deltaCents))
    .slice(0, 4);

  const observation = buildObservation(monthTotal, tilesData);

  return (
    <>
      <header className="flex items-center justify-between">
        <h1 className="font-serif text-3xl text-ink lowercase-title">home</h1>
        <Link
          to="/breakdown"
          className="inline-flex items-center gap-1 text-sm text-moss hover:text-moss-deep transition-colors"
        >
          <ArrowUpRight className="h-3.5 w-3.5" />
          <span>Breakdown</span>
        </Link>
      </header>

      <section className="mt-12">
        <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          {formatMonth().toLowerCase()}
        </p>
        <p className="mt-3 font-serif text-[4rem] leading-none text-ink tabular">
          {formatMoney(monthTotal)}
        </p>

        <div className="mt-5 inline-flex items-center gap-2 rounded-full bg-warn-wash px-3 py-1 text-xs text-warn">
          <span className="tabular font-medium">
            {formatPercent(deltaPct)} vs your avg
          </span>
        </div>

        <p className="mt-6 max-w-[28ch] font-serif italic text-[0.95rem] leading-relaxed text-ink-secondary">
          {observation}
        </p>
      </section>

      <section className="mt-12 grid grid-cols-2 gap-3">
        {tilesData.map((d) => (
          <DeltaTile
            key={d.category}
            layout="stacked"
            tone="neutral"
            category={d.category.toLowerCase()}
            delta={Math.round(d.deltaCents / 100)}
          />
        ))}
        {/* Catch-all subscriptions tile only appears when no subscription spend
            this month would have surfaced it in tilesData — otherwise we'd
            render the category twice. */}
        {!tilesData.some((d) => d.category === "Subscriptions") && (
          <DeltaTile
            layout="stacked"
            tone="neutral"
            direction="neutral"
            category="subscriptions"
            delta={0}
            band="steady"
          />
        )}
      </section>
    </>
  );
}

function buildObservation(
  monthTotal: number,
  tiles: { category: Category; deltaCents: number }[]
): string {
  if (tiles.length === 0) return "a quiet start to the month.";
  const above = tiles.filter((t) => t.deltaCents > 0);
  const below = tiles.filter((t) => t.deltaCents < 0);

  if (above.length > 0 && monthTotal > TOTAL_BASELINE) {
    const top = above[0];
    return `${top.category.toLowerCase()} is doing most of the lifting this month.`;
  }
  if (below.length > above.length) {
    return "you're spending more deliberately than usual.";
  }
  return "things are roughly where they always sit.";
}

/* ─── Empty ──────────────────────────────────────────────────── */

function EmptyHome({
  showHint,
  onDismissHint,
}: {
  showHint: boolean;
  onDismissHint: () => void;
}) {
  return (
    <>
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">home</h1>
      </header>

      <div className="mt-20 flex flex-col items-center text-center">
        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-sunken text-ink-tertiary">
          <Wallet className="h-8 w-8" strokeWidth={1.5} />
        </div>
        <h2 className="mt-6 font-serif text-2xl text-ink lowercase-title">
          your ledger is empty
        </h2>
        <p className="mt-2 max-w-[26ch] text-sm text-ink-secondary">
          tap the chat button below to log your first transaction.
        </p>
      </div>

      {/* Faded ghost tiles */}
      <div className="mt-12 grid grid-cols-2 gap-3 opacity-30">
        <GhostTile />
        <GhostTile />
      </div>

      {/* Pulsing ring around chat button (rendered via fixed pos to align with BottomNav) */}
      <ChatButtonPulse />

      {showHint && (
        <FirstHintStrip onDismiss={onDismissHint} />
      )}
    </>
  );
}

function GhostTile() {
  return (
    <div className="rounded-2xl border border-hairline bg-sunken/40 p-4 min-h-[6.5rem]">
      <div className="h-3 w-16 rounded-full bg-ink-quaternary/30" />
      <div className="mt-4 h-6 w-20 rounded-full bg-ink-quaternary/30" />
      <div className="mt-2 h-2 w-12 rounded-full bg-ink-quaternary/20" />
    </div>
  );
}

/**
 * A subtle pulse ring positioned to surround the BottomNav's center chat
 * button. The button itself is in BottomNav; this is a sibling halo.
 */
function ChatButtonPulse() {
  return (
    <div className="pointer-events-none fixed bottom-0 left-1/2 z-30 -translate-x-1/2 md:hidden">
      <div className="relative h-16 w-16 -translate-y-[1.7rem]">
        <span className="absolute inset-0 animate-ping-soft rounded-full bg-moss/30" />
      </div>
    </div>
  );
}

function FirstHintStrip({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="pointer-events-auto fixed bottom-20 left-1/2 z-40 w-[min(92vw,22rem)] -translate-x-1/2 rounded-2xl border border-hairline bg-elevated px-4 py-3 md:hidden animate-fade-up">
      <p className="font-serif italic text-sm text-ink-secondary lowercase-title">
        type or speak your first transaction
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {PREFILL_CHIPS.map((chip) => (
          <Link
            key={chip}
            to="/chat"
            onClick={onDismiss}
            className={cn(
              "rounded-full border border-hairline bg-surface px-3 py-1 text-xs text-ink-secondary",
              "transition-colors hover:bg-sunken/60 hover:text-ink"
            )}
          >
            {chip}
          </Link>
        ))}
        <button
          type="button"
          onClick={onDismiss}
          className="ml-auto text-[0.7rem] text-ink-tertiary hover:text-ink-secondary"
        >
          dismiss
        </button>
      </div>
    </div>
  );
}
