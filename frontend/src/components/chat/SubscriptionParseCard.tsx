import { useState } from "react";
import { Check, Pause, X } from "lucide-react";
import type { Card } from "@/lib/fixtures";
import { CATEGORIES, type Category } from "@/lib/categories";
import type { SubscriptionParseDraft } from "@/lib/chat";
import { cn } from "@/lib/utils";

/**
 * Chat-side subscription parse card — Day 19. Sister of `ParseCard`
 * (transactions) and `CardParseCard` (cards) with the recurring-charge
 * field set.
 *
 * UX contract (DESIGN.md §6.5 / §8.3):
 *   - Headline shows name + amount + frequency + next billing date.
 *   - `frequency` and `start_date` render read-only (immutability rule
 *     §8.3) with a small "to change cadence, cancel and re-add" hint.
 *   - Editable inline: `amount`, `category`, `card` (with "no card / ACH"
 *     option), `name`.
 *   - Forward-only micro-text: "first auto-log on {date}; today's
 *     charge isn't backfilled" — sets expectations per §6.5.
 *   - Terminal states (cancelled / paused) render the same badge family
 *     as the other parse cards.
 */

const FREQUENCIES: { value: SubscriptionParseDraft["frequency"]; label: string }[] = [
  { value: "weekly", label: "weekly" },
  { value: "monthly", label: "monthly" },
  { value: "quarterly", label: "quarterly" },
  { value: "annual", label: "annual" },
];

interface SubscriptionParseCardProps {
  preface?: string;
  draft: SubscriptionParseDraft;
  /** Active cards the user can assign — for the card picker. */
  cards: Card[];
  /** When set, the card is locked into "tracking" / "paused" / "cancelled". */
  committed: boolean;
  /**
   * Lifecycle of the committed subscription row. `'active'` →
   * `tracking.`; `'paused'` → `paused.`; `'cancelled'` → `cancelled.`.
   */
  committedState?: "active" | "paused" | "cancelled";
  /**
   * `true` for rehydrated read-only history. Mirrors `ParseCardProps.frozen`.
   */
  frozen?: boolean;
  /**
   * `true` between offline "looks right" tap and drain. Mirrors
   * `ParseCardProps.pendingSync`.
   */
  pendingSync?: boolean;
  onConfirm: (draft: SubscriptionParseDraft) => void;
}

export function SubscriptionParseCard({
  preface,
  draft,
  cards,
  committed,
  committedState,
  frozen,
  pendingSync,
  onConfirm,
}: SubscriptionParseCardProps) {
  const [local, setLocal] = useState<SubscriptionParseDraft>(draft);

  const amountValid = /^\d+(?:\.\d{1,2})?$/.test(local.amount.trim());
  const canConfirm = amountValid && !committed && !frozen && !pendingSync;

  const isTracking = committed && committedState !== "cancelled";
  const isCancelled = committed && committedState === "cancelled";
  const isOfflinePending = !committed && !!pendingSync;
  const isNotSaved = !committed && !!frozen && !pendingSync;

  return (
    <div className="w-full max-w-[88%] animate-slide-up-in">
      {preface && (
        <p className="mb-2 px-1 text-[0.95rem] leading-relaxed text-ink">
          {preface}
        </p>
      )}

      <div
        className={cn(
          "rounded-2xl border bg-elevated px-4 py-4",
          committed || frozen
            ? "border-moss-soft/60"
            : "border-moss-soft ring-1 ring-moss/20",
          (isCancelled || isNotSaved) && "opacity-75",
        )}
      >
        {/* Headline */}
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-serif text-lg text-ink lowercase-title">
            {local.name}
          </span>
          <span className="tabular text-base text-ink">
            ${local.amount} · {local.frequency}
          </span>
        </div>

        {/* Editable rows — skipped for rehydrated/committed/pending. */}
        {!committed && !frozen && !pendingSync && (
          <div className="mt-4 grid grid-cols-2 gap-3 border-t border-hairline pt-3">
            <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
              amount
              <input
                type="text"
                inputMode="decimal"
                value={local.amount}
                onChange={(e) =>
                  setLocal({
                    ...local,
                    amount: e.target.value.replace(/[^\d.]/g, ""),
                  })
                }
                placeholder="0.00"
                className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink placeholder:text-ink-quaternary focus:outline-none"
              />
            </label>
            <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
              category
              <select
                value={local.category}
                onChange={(e) =>
                  setLocal({
                    ...local,
                    category: e.target.value as Category,
                  })
                }
                className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="col-span-2 flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
              card
              <select
                value={local.cardId ?? ""}
                onChange={(e) =>
                  setLocal({
                    ...local,
                    cardId: e.target.value === "" ? null : e.target.value,
                  })
                }
                className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none"
              >
                <option value="">no card · bank ACH</option>
                {cards.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} {c.last4 ? `···· ${c.last4}` : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        {/* Read-only frequency / start-date row — present on every state
            so the user always sees what's fixed and why. */}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-3 text-[0.78rem] text-ink-tertiary">
          <span>
            starts <span className="text-ink-secondary">{local.startDate}</span>
          </span>
          <span>
            first auto-log{" "}
            <span className="text-ink-secondary">{local.nextBillingDate}</span>
          </span>
        </div>

        {!committed && !frozen && !pendingSync && (
          <p className="mt-2 text-[0.7rem] text-ink-quaternary">
            today's charge isn't backfilled — log it manually if you want
            it in the ledger. cancel and re-add to change cadence.
          </p>
        )}

        {/* Action / badge area. */}
        {isTracking && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-moss-deep">
            {committedState === "paused" ? (
              <>
                <Pause className="h-3.5 w-3.5" />
                <span>paused.</span>
              </>
            ) : (
              <>
                <Check className="h-3.5 w-3.5" />
                <span>tracking.</span>
              </>
            )}
          </div>
        )}
        {isCancelled && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <X className="h-3.5 w-3.5" />
            <span>cancelled.</span>
          </div>
        )}
        {isNotSaved && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>not saved.</span>
          </div>
        )}
        {isOfflinePending && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>queued — syncs when online.</span>
          </div>
        )}
        {!committed && !frozen && !pendingSync && (
          <>
            <button
              type="button"
              onClick={() => onConfirm(local)}
              disabled={!canConfirm}
              className="mt-4 h-11 w-full rounded-2xl bg-moss text-[0.95rem] font-medium text-surface transition-colors hover:bg-moss-deep disabled:cursor-not-allowed disabled:opacity-50"
            >
              looks right
            </button>
            {!amountValid && (
              <p className="mt-2 text-center text-[0.72rem] text-ink-tertiary">
                amount has to be a positive number.
              </p>
            )}
          </>
        )}

        {/* Frequency picker (for re-create flow): only shown to indicate
            the user CAN'T edit it inline. Disabled select. */}
        {!committed && !frozen && !pendingSync && (
          <div className="mt-3 flex items-center gap-1.5 text-[0.7rem] text-ink-quaternary">
            <span>cadence:</span>
            <select
              disabled
              value={local.frequency}
              className="rounded-md border border-hairline bg-sunken px-1.5 py-0.5 text-[0.7rem] text-ink-tertiary"
            >
              {FREQUENCIES.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
    </div>
  );
}
