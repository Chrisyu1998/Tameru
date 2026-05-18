import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Trash2, AlertTriangle, ArrowRight } from "lucide-react";
import { SketchIcon } from "@/components/SketchIcon";
import { Pill } from "@/components/Pill";
import { PendingDeleteProgress } from "@/components/PendingDeleteProgress";
import { ledger, useLedger } from "@/lib/ledger";
import {
  MEMORY_CAPACITY,
  MEMORY_CATEGORY_LABELS,
  type MemoryCategory,
  type MemoryFactRow,
} from "@/lib/memory";
import { cn } from "@/lib/utils";

const LONG_PRESS_MS = 500;

export default function MemoryPage() {
  // Memory state + pending-delete timers live in the ledger store at
  // module scope. Navigating away during the undo window still commits
  // the DELETE (parity with cards/transactions). The page itself owns
  // only ephemeral UI state — the "armed" row for the confirm prompt
  // and a loading-error message.
  const { memory, pendingMemoryDeletes } = useLedger();
  const [armedId, setArmedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasLoaded, setHasLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await ledger.refreshMemory();
        if (!cancelled) setHasLoaded(true);
      } catch (err) {
        if (cancelled) return;
        setError((err as Error).message);
        setHasLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const used = memory.length;
  const overEighty = used / MEMORY_CAPACITY > 0.8;

  return (
    <div className="mx-auto w-full max-w-2xl px-5 pt-8 pb-20">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          ai memory
        </h1>
        <p className="mt-2 text-sm text-ink-secondary">
          what tameru remembers about you · you can edit or remove anything.
        </p>
      </header>

      {/* Capacity row */}
      <CapacityRow used={used} overEighty={overEighty} />

      {error && (
        <p className="mt-6 rounded-xl bg-warn-wash px-4 py-3 text-sm text-ink-secondary">
          {error}
        </p>
      )}

      {/* Facts grid */}
      {!hasLoaded ? (
        <p className="mt-10 text-center text-sm text-ink-tertiary">loading…</p>
      ) : memory.length === 0 ? (
        <p className="mt-10 text-center text-sm text-ink-tertiary">
          tameru hasn't remembered anything yet — facts get added as you chat.
        </p>
      ) : (
        <ul className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {memory.map((fact) => {
            const pending = pendingMemoryDeletes[fact.id];
            return (
              <FactTile
                key={fact.id}
                fact={fact}
                armed={armedId === fact.id && !pending}
                dimmed={armedId !== null && armedId !== fact.id && !pending}
                pending={pending}
                onArm={() => setArmedId(fact.id)}
                onCancel={() => setArmedId(null)}
                onConfirmDelete={() => {
                  setArmedId(null);
                  ledger.scheduleDeleteMemory(fact.id);
                }}
                onUndo={() => ledger.undoDeleteMemory(fact.id)}
                longPressMs={LONG_PRESS_MS}
              />
            );
          })}
        </ul>
      )}

      {/* Hint footer */}
      <div className="mt-10 border-t border-hairline pt-6">
        <Link
          to="/chat"
          className="inline-flex items-center gap-2 text-[0.85rem] text-ink-secondary hover:text-ink"
        >
          <SketchIcon kind="sparkle" size={14} seed={59} className="text-moss" />
          <span>correct or add facts via tameru ai</span>
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  );
}

function CapacityRow({
  used,
  overEighty,
}: {
  used: number;
  overEighty: boolean;
}) {
  const pct = Math.min(100, (used / MEMORY_CAPACITY) * 100);
  return (
    <div className="mt-6 rounded-2xl border border-hairline bg-surface px-4 py-3">
      <div className="flex items-center justify-between">
        <span className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          capacity
        </span>
        <span className="tabular text-[0.85rem] text-ink-secondary">
          {used} / {MEMORY_CAPACITY} facts
        </span>
      </div>
      <div className="mt-2 h-1 overflow-hidden rounded-full bg-sunken">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            overEighty ? "bg-warn" : "bg-moss",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      {overEighty && (
        <div className="mt-2 flex items-start gap-2 rounded-xl bg-warn-wash px-3 py-2 text-[0.78rem] text-ink-secondary">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-warn" />
          <span>
            memory is more than 80% full. older facts may be forgotten as new
            ones come in — tidy up anything you no longer need.
          </span>
        </div>
      )}
    </div>
  );
}

interface PendingState {
  scheduledAt: number;
  durationMs: number;
}

function FactTile({
  fact,
  armed,
  dimmed,
  pending,
  onArm,
  onCancel,
  onConfirmDelete,
  onUndo,
  longPressMs,
}: {
  fact: MemoryFactRow;
  armed: boolean;
  dimmed: boolean;
  pending: PendingState | undefined;
  onArm: () => void;
  onCancel: () => void;
  onConfirmDelete: () => void;
  onUndo: () => void;
  longPressMs: number;
}) {
  const timerRef = useRef<number | null>(null);

  const startLongPress = () => {
    if (armed || pending) return;
    timerRef.current = window.setTimeout(() => {
      onArm();
      timerRef.current = null;
    }, longPressMs);
  };
  const cancelLongPress = () => {
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };
  useEffect(() => () => cancelLongPress(), []);

  const label = useMemo(
    () =>
      MEMORY_CATEGORY_LABELS[fact.category as MemoryCategory] ?? fact.category,
    [fact.category],
  );
  const provenance = useMemo(() => formatProvenance(fact.reinforced_at), [
    fact.reinforced_at,
  ]);

  return (
    <li
      onPointerDown={pending ? undefined : startLongPress}
      onPointerUp={cancelLongPress}
      onPointerLeave={cancelLongPress}
      onClick={pending ? onUndo : undefined}
      className={cn(
        "relative overflow-hidden rounded-2xl border bg-surface px-4 py-3 transition-all",
        armed
          ? "border-over bg-over-wash"
          : pending
            ? "border-hairline opacity-55 cursor-pointer"
            : "border-hairline hover:bg-elevated",
        dimmed && "opacity-40",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <Pill tone={armed ? "over" : "moss"}>{label}</Pill>
        {!armed && !pending && (
          <button
            type="button"
            onClick={onArm}
            aria-label="remove fact"
            className="flex h-7 w-7 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken hover:text-over"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <p
        className={cn(
          "mt-2 text-[0.95rem] leading-snug text-ink",
          pending && "line-through decoration-1",
        )}
      >
        {fact.fact}
      </p>

      {pending ? (
        <p className="mt-2 text-[0.72rem] text-moss-deep tabular">
          deleting · tap to undo
        </p>
      ) : (
        <p className="mt-2 text-[0.7rem] text-ink-tertiary">{provenance}</p>
      )}

      {armed && !pending && (
        <div className="mt-3 flex items-center gap-3 border-t border-over/30 pt-2 text-[0.8rem]">
          <button
            type="button"
            onClick={onConfirmDelete}
            className="font-medium text-over hover:underline"
          >
            remove this fact
          </button>
          <span className="text-ink-quaternary">·</span>
          <button
            type="button"
            onClick={onCancel}
            className="text-ink-secondary hover:text-ink"
          >
            cancel
          </button>
        </div>
      )}

      {pending && (
        <PendingDeleteProgress
          scheduledAt={pending.scheduledAt}
          durationMs={pending.durationMs}
        />
      )}
    </li>
  );
}

function formatProvenance(reinforcedAt: string): string {
  const reinforced = new Date(reinforcedAt);
  if (Number.isNaN(reinforced.getTime())) {
    return "saved from chat";
  }
  const ageMs = Date.now() - reinforced.getTime();
  const dayMs = 1000 * 60 * 60 * 24;
  const days = Math.floor(ageMs / dayMs);
  if (days <= 0) return "reinforced today";
  if (days === 1) return "reinforced 1 day ago";
  if (days < 30) return `reinforced ${days} days ago`;
  const months = Math.floor(days / 30);
  if (months === 1) return "reinforced 1 month ago";
  return `reinforced ${months} months ago`;
}
