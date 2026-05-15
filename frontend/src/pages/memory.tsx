import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Trash2, AlertTriangle, ArrowRight } from "lucide-react";
import { SketchIcon } from "@/components/SketchIcon";
import { Pill } from "@/components/Pill";
import { UndoToast, type PendingDelete } from "@/components/UndoToast";
import {
  MEMORY_CAPACITY,
  initialMemoryFacts,
  type MemoryFact,
} from "@/lib/memory";
import { cn } from "@/lib/utils";

const LONG_PRESS_MS = 500;

export default function MemoryPage() {
  const [facts, setFacts] = useState<MemoryFact[]>(initialMemoryFacts);
  const [armedId, setArmedId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingDelete | null>(null);
  // Stash the most recently removed fact so undo can restore it in place.
  const lastRemovedRef = useRef<{ index: number; fact: MemoryFact } | null>(
    null
  );

  const used = facts.length;
  const ratio = used / MEMORY_CAPACITY;
  const overEighty = ratio > 0.8;

  const requestDelete = (fact: MemoryFact) => {
    const index = facts.findIndex((f) => f.id === fact.id);
    if (index === -1) return;
    lastRemovedRef.current = { index, fact };
    // Remove from list immediately; UndoToast handles 5s commit/undo.
    setFacts((prev) => prev.filter((f) => f.id !== fact.id));
    setArmedId(null);
    setPending({
      id: fact.id,
      label: fact.text,
      // Commit is a no-op for the mock store — removal already happened.
      commit: () => {
        lastRemovedRef.current = null;
      },
    });
  };

  const undoDelete = () => {
    const stash = lastRemovedRef.current;
    if (stash) {
      setFacts((prev) => {
        const next = [...prev];
        next.splice(stash.index, 0, stash.fact);
        return next;
      });
      lastRemovedRef.current = null;
    }
    setPending(null);
  };

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

      {/* Facts grid */}
      <ul className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {facts.map((fact) => (
          <FactTile
            key={fact.id}
            fact={fact}
            armed={armedId === fact.id}
            dimmed={armedId !== null && armedId !== fact.id}
            onArm={() => setArmedId(fact.id)}
            onCancel={() => setArmedId(null)}
            onConfirmDelete={() => requestDelete(fact)}
            longPressMs={LONG_PRESS_MS}
          />
        ))}
      </ul>

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

      <UndoToast
        pending={pending}
        onUndo={undoDelete}
        onTimeout={() => setPending(null)}
      />
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
            overEighty ? "bg-warn" : "bg-moss"
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

function FactTile({
  fact,
  armed,
  dimmed,
  onArm,
  onCancel,
  onConfirmDelete,
  longPressMs,
}: {
  fact: MemoryFact;
  armed: boolean;
  dimmed: boolean;
  onArm: () => void;
  onCancel: () => void;
  onConfirmDelete: () => void;
  longPressMs: number;
}) {
  const timerRef = useRef<number | null>(null);

  const startLongPress = () => {
    if (armed) return;
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

  return (
    <li
      onPointerDown={startLongPress}
      onPointerUp={cancelLongPress}
      onPointerLeave={cancelLongPress}
      className={cn(
        "relative rounded-2xl border bg-surface px-4 py-3 transition-all",
        armed
          ? "border-over bg-over-wash"
          : "border-hairline hover:bg-elevated",
        dimmed && "opacity-40"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <Pill tone={armed ? "over" : "moss"}>{fact.category}</Pill>
        {!armed && (
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

      <p className="mt-2 text-[0.95rem] leading-snug text-ink">{fact.text}</p>

      <p className="mt-2 text-[0.7rem] text-ink-tertiary">{fact.provenance}</p>

      {armed && (
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
    </li>
  );
}
