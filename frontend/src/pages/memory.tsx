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
  // Oldest reinforced timestamp drives the "reinforced N days ago" line in
  // CapacityRow — the user-facing signal that Day 17's 90-day decay sweep
  // will start trimming soon. Computed here from the rows already in the
  // ledger so no extra API call is needed.
  const oldestReinforcedAt = memory.reduce<string | null>((oldest, fact) => {
    if (oldest === null || fact.reinforced_at < oldest) return fact.reinforced_at;
    return oldest;
  }, null);

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
      <CapacityRow
        used={used}
        overEighty={overEighty}
        oldestReinforcedAt={oldestReinforcedAt}
      />

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
  oldestReinforcedAt,
}: {
  used: number;
  overEighty: boolean;
  oldestReinforcedAt: string | null;
}) {
  const pct = Math.min(100, (used / MEMORY_CAPACITY) * 100);
  const oldestDaysAgo =
    oldestReinforcedAt === null ? null : daysSince(oldestReinforcedAt);
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
      {oldestDaysAgo !== null && (
        <div className="mt-2 text-[0.75rem] text-ink-tertiary">
          oldest fact reinforced {formatDaysAgo(oldestDaysAgo)}
        </div>
      )}
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
  const days = daysSince(reinforcedAt);
  if (days === null) return "saved from chat";
  return `reinforced ${formatDaysAgo(days)}`;
}

// Whole-day count from an ISO timestamp to "now". Negative ages clamp to
// 0 — a clock-skew reinforced_at in the future would otherwise show as
// "-3 days ago." Returns null when the timestamp is unparseable so the
// caller can fall back to a non-time string.
function daysSince(iso: string): number | null {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const dayMs = 1000 * 60 * 60 * 24;
  return Math.max(0, Math.floor((Date.now() - t) / dayMs));
}

// Shared age formatter — drops the "reinforced" prefix so the caller can
// compose it ("reinforced N days ago" vs "oldest fact reinforced N days
// ago"). Months threshold (30) matches the recency-decay constant in the
// pg_cron prune scoring (DESIGN.md §7.6).
function formatDaysAgo(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months === 1) return "1 month ago";
  return `${months} months ago`;
}
