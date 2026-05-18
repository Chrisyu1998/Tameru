import { useEffect, useState } from "react";
import { Button } from "@/components/Button";
import { BottomSheet } from "@/components/BottomSheet";
import { Pill } from "@/components/Pill";
import { ApiError } from "@/lib/api";
import { ledger } from "@/lib/ledger";
import {
  GOAL_OVERALL_LABEL,
  GOAL_PERIOD_LABELS,
  type GoalPatch,
  type GoalPeriod,
  type GoalWithSpend,
} from "@/lib/goalsApi";
import { cn } from "@/lib/utils";

interface EditGoalSheetProps {
  open: boolean;
  goal: GoalWithSpend | null;
  onClose: () => void;
  /** Called when user taps delete; parent triggers the on-row undo timer. */
  onRequestDelete: (goal: GoalWithSpend) => void;
}

const PERIOD_OPTIONS: GoalPeriod[] = ["week", "month", "year"];

/**
 * Edit a goal's mutable fields: amount and period. Category is fixed by
 * the `(user, category, period)` unique key — to move a goal between
 * categories the user deletes and asks chat to set a new one. Mirrors
 * `EditCardSheet`'s shape so the surfaces feel identical.
 */
export function EditGoalSheet({
  open,
  goal,
  onClose,
  onRequestDelete,
}: EditGoalSheetProps) {
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState<GoalPeriod>("month");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!goal) return;
    setAmount(goal.goal.amount);
    setPeriod(goal.goal.period);
    setError(null);
    setSaving(false);
  }, [goal]);

  if (!goal) {
    return (
      <BottomSheet open={open} onClose={onClose} desktopVariant="side">
        {null}
      </BottomSheet>
    );
  }

  const trimmedAmount = amount.trim();
  const parsedAmount = parseFloat(trimmedAmount);
  const amountValid = Number.isFinite(parsedAmount) && parsedAmount > 0;
  const amountChanged = trimmedAmount !== goal.goal.amount;
  const periodChanged = period !== goal.goal.period;
  const dirty = amountChanged || periodChanged;
  const valid = amountValid;

  const categoryLabel = goal.goal.category ?? GOAL_OVERALL_LABEL;

  const save = async () => {
    if (!dirty || !valid || saving) return;
    const patch: GoalPatch = {};
    if (amountChanged) patch.amount = parsedAmount.toFixed(2);
    if (periodChanged) patch.period = period;
    setSaving(true);
    setError(null);
    try {
      await ledger.updateGoal(goal.goal.id, patch);
      // Period change shifts the window — re-pull so spend matches.
      // Suppress refresh failure here: the optimistic local update has
      // already landed, so the sheet can close cleanly even if the
      // follow-up GET fails; the /goals page will surface the error
      // on its own mount path.
      if (periodChanged) {
        ledger.refreshGoals().catch(() => {});
      }
      onClose();
    } catch (err) {
      setSaving(false);
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "you already have a goal for this category and period. delete it first, or pick a different period.",
        );
        return;
      }
      setError("couldn't save changes. try again?");
    }
  };

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      ariaLabel="edit goal"
      desktopVariant="side"
    >
      <h2 className="font-serif text-xl text-ink lowercase-title">edit goal</h2>

      <div className="mt-5 flex flex-col gap-4">
        <FieldGroup label="category (not editable)">
          <div className="flex items-center justify-between">
            <Pill tone="moss">{categoryLabel}</Pill>
            <span className="text-[0.7rem] text-ink-quaternary">
              delete + re-add to move
            </span>
          </div>
        </FieldGroup>

        <FieldGroup label="amount">
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">$</span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="0"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </FieldGroup>

        <FieldGroup label="period">
          <div className="mt-1 grid grid-cols-3 gap-1.5">
            {PERIOD_OPTIONS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPeriod(p)}
                className={cn(
                  "rounded-xl border px-2 py-2 text-[0.85rem] transition-colors",
                  p === period
                    ? "border-moss bg-moss-wash/60 text-ink"
                    : "border-hairline bg-surface text-ink-secondary hover:bg-elevated",
                )}
              >
                {GOAL_PERIOD_LABELS[p]}
              </button>
            ))}
          </div>
        </FieldGroup>

        {error && (
          <p className="rounded-xl bg-warn-wash px-4 py-3 text-sm text-ink-secondary">
            {error}
          </p>
        )}
      </div>

      <div className="mt-7 flex flex-col gap-3">
        <Button fullWidth disabled={!dirty || !valid || saving} onClick={save}>
          {saving ? "saving…" : "save changes"}
        </Button>
        <button
          type="button"
          onClick={() => onRequestDelete(goal)}
          className="self-center text-sm text-over hover:underline underline-offset-4"
        >
          delete goal
        </button>
      </div>
    </BottomSheet>
  );
}

function FieldGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-hairline bg-surface px-4 py-3">
      <p className="text-[0.65rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </p>
      <div className="mt-1">{children}</div>
    </div>
  );
}
