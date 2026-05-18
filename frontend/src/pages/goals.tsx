import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Pill } from "@/components/Pill";
import { SwipeableRow } from "@/components/SwipeableRow";
import { SketchIcon } from "@/components/SketchIcon";
import { SketchIllustration } from "@/components/SketchIllustration";
import { PendingDeleteProgress } from "@/components/PendingDeleteProgress";
import { EditGoalSheet } from "@/components/EditGoalSheet";
import { AIHintFooter } from "@/pages/cards";
import { ledger, useLedger } from "@/lib/ledger";
import { setChatSeed } from "@/lib/chatSeed";
import {
  GOAL_OVERALL_LABEL,
  GOAL_PERIOD_LABELS,
  type GoalWithSpend,
} from "@/lib/goalsApi";
import { cn } from "@/lib/utils";

export default function GoalsPage() {
  const navigate = useNavigate();
  const { goals, pendingGoalDeletes } = useLedger();
  const [editing, setEditing] = useState<GoalWithSpend | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasLoaded, setHasLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await ledger.refreshGoals();
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

  const askToAddGoal = () => {
    setChatSeed("set a monthly budget for ");
    navigate("/chat");
  };

  const requestDelete = (goal: GoalWithSpend) => {
    setEditing(null);
    ledger.scheduleDeleteGoal(goal.goal.id);
  };

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-8 pb-24">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">goals</h1>
        <p className="mt-2 text-sm text-ink-tertiary">
          tap a goal to edit. swipe left to remove.
        </p>
      </header>

      {error && (
        <p className="mt-6 rounded-xl bg-warn-wash px-4 py-3 text-sm text-ink-secondary">
          {error}
        </p>
      )}

      {!hasLoaded ? (
        <p className="mt-10 text-center text-sm text-ink-tertiary">loading…</p>
      ) : goals.length === 0 ? (
        <EmptyGoals onAsk={askToAddGoal} />
      ) : (
        <ul className="mt-6 flex flex-col gap-3">
          {goals.map((g) => {
            const pending = pendingGoalDeletes[g.goal.id];
            return (
              <li key={g.goal.id}>
                <SwipeableRow
                  onConfirmDelete={() => requestDelete(g)}
                  onEdit={() => setEditing(g)}
                >
                  <button
                    type="button"
                    onClick={() =>
                      pending
                        ? ledger.undoDeleteGoal(g.goal.id)
                        : setEditing(g)
                    }
                    className={cn(
                      "block w-full text-left transition-opacity",
                      pending && "opacity-55",
                    )}
                  >
                    <GoalTile goal={g} pending={!!pending} />
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
        label="ask tameru to set a goal"
        onClick={askToAddGoal}
      />

      <EditGoalSheet
        open={editing !== null}
        goal={editing}
        onClose={() => setEditing(null)}
        onRequestDelete={(goal) => {
          setEditing(null);
          ledger.scheduleDeleteGoal(goal.goal.id);
        }}
      />
    </div>
  );
}

/**
 * One goal row. Renders category pill, amount + period, and a progress
 * bar tinted moss under 80%, amber 80-100%, over (warn) at 100%+. The
 * over-budget bar caps the fill at 100% so the visual stays consistent
 * regardless of overshoot; the spend text tells the actual story.
 */
function GoalTile({
  goal,
  pending,
}: {
  goal: GoalWithSpend;
  pending: boolean;
}) {
  const categoryLabel = goal.goal.category ?? GOAL_OVERALL_LABEL;
  const amount = parseFloat(goal.goal.amount);
  const spent = parseFloat(goal.spent_period_to_date);
  const ratio = goal.progress_ratio;
  const fillPct = Math.min(100, Math.max(0, ratio * 100));
  const tone =
    ratio >= 1 ? "over" : ratio >= 0.8 ? "warn" : "moss";

  return (
    <div className="rounded-2xl border border-hairline bg-surface px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-3">
        <Pill tone={goal.goal.category ? "moss" : "ink"}>{categoryLabel}</Pill>
        <span
          className={cn(
            "tabular text-[0.78rem] text-ink-tertiary",
            pending && "line-through decoration-1",
          )}
        >
          ${amount.toFixed(amount % 1 === 0 ? 0 : 2)} /{" "}
          {GOAL_PERIOD_LABELS[goal.goal.period]}
        </span>
      </div>

      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-sunken">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            tone === "over" && "bg-over",
            tone === "warn" && "bg-warn",
            tone === "moss" && "bg-moss",
          )}
          style={{ width: `${fillPct}%` }}
        />
      </div>

      {pending ? (
        <p className="mt-2 text-[0.72rem] text-moss-deep tabular">
          deleting · tap to undo
        </p>
      ) : (
        <p className="mt-2 text-[0.78rem] text-ink-tertiary tabular">
          ${spent.toFixed(spent % 1 === 0 ? 0 : 2)} of $
          {amount.toFixed(amount % 1 === 0 ? 0 : 2)} this{" "}
          {goal.goal.period}
        </p>
      )}
    </div>
  );
}

function EmptyGoals({ onAsk }: { onAsk: () => void }) {
  return (
    <div className="mt-12 flex flex-col items-center text-center">
      <SketchIllustration
        kind="empty-list"
        size={108}
        className="text-ink-tertiary"
      />
      <p className="mt-4 font-serif text-xl text-ink lowercase-title">
        no budgets yet
      </p>
      <p className="mt-1 max-w-[28ch] text-[0.85rem] text-ink-tertiary">
        set a budget and tameru will track how close you are.
      </p>
      <button
        type="button"
        onClick={onAsk}
        className="mt-5 inline-flex h-11 items-center gap-2 rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
      >
        <SketchIcon kind="sparkle" size={16} seed={9} />
        ask tameru ai to set one
      </button>
    </div>
  );
}
