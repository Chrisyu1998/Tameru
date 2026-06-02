import { AlertCircle, Calendar, Check, CreditCard, Tag, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cardLabel, type ParseDraft } from "@/lib/chat";
import type { Card } from "@/lib/fixtures";
import { useCategoryLabel } from "@/lib/categories";
import { formatMoney, formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

// "Other / Cash" is the always-available choice — represented by an empty
// cardId on the draft, persisted server-side as card_id = NULL. Real cards
// from useLedger() resolve via cardLabel(); an empty cardId or a missing
// card row both render as "Other".

interface ParseCardProps {
  preface?: string;
  draft: ParseDraft;
  /** Live cards from `useLedger()` — used to resolve `draft.cardId` to a name. */
  cards: Card[];
  /** When set, the card is locked into "logged" state. */
  committed: boolean;
  /**
   * Lifecycle of the committed row when the card was confirmed (only
   * meaningful with `committed=true`). `'active'` → render `logged.`;
   * `'deleted'` → render `deleted.` in a muted style. Undefined defaults
   * to `'active'` so legacy in-session commits behave unchanged.
   */
  committedState?: "active" | "deleted";
  /**
   * `true` for parse cards rehydrated from history. The card becomes a
   * read-only historical artifact (no buttons, no badges that imply
   * pending action). DESIGN.md §8 status-column doctrine: the proposal
   * payload is frozen at confirm time.
   */
  frozen?: boolean;
  /**
   * `true` between the user tapping "looks right" while offline (queued
   * in `offline_queue.ts`) and the drain landing on a terminal outcome.
   * Hides the action buttons and shows a `queued — syncs when online`
   * micro-badge in their place. The global "X pending sync" banner
   * counts the same entries.
   */
  pendingSync?: boolean;
  onConfirm: (draft: ParseDraft) => void;
  onFix: () => void;
}

/**
 * The primary commit surface.
 *
 * Fields are display-only. All editing flows through `let me fix it` →
 * `EditTransactionSheet`, which writes back to `chatStore.messages[].draft`.
 * The store is the single source of truth — ParseCard renders whatever the
 * latest draft prop is, no local copy.
 *
 * Three render modes:
 *   - **fresh, uncommitted** (`!committed && !frozen`) — "looks right" +
 *     "let me fix it" buttons. The original Day 9 surface, now display-only
 *     for the fields themselves.
 *   - **committed** (`committed`) — locked into the badge state:
 *     `logged.` when `committedState === 'active'` (default), or
 *     `deleted.` when the row was soft-deleted after confirm.
 *   - **rehydrated, never confirmed** (`!committed && frozen`) — read-only
 *     with a `not saved.` badge.
 */
export function ParseCard({
  preface,
  draft,
  cards,
  committed,
  committedState,
  frozen,
  pendingSync,
  onConfirm,
  onFix,
}: ParseCardProps) {
  const { t } = useTranslation();
  const catLabel = useCategoryLabel();
  const card = cardLabel(draft.cardId, cards);

  // Lower confidence → muted warning glyph next to the field. No edit
  // affordance on the card itself; users tap "let me fix it" to correct.
  const lowConf = (v: number) => v < 0.75;

  // Resolve the badge state. `committed && committedState === 'deleted'`
  // is the deleted-after-confirm case; everything else with `committed`
  // is the standard logged-active case.
  const isDeleted = committed && committedState === "deleted";
  const isLogged = committed && !isDeleted;
  // Rehydrated but never confirmed — historical proposal the user
  // closed the app on before tapping "looks right."
  const isCancelled = !committed && !!frozen && !pendingSync;
  // Queued offline-confirm, waiting for drain.
  const isPending = !committed && !!pendingSync;

  const cardDisplay =
    draft.cardId && card.last4 !== "—"
      ? `${card.name} · ${card.last4}`
      : t("chat.parseCard.other");

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
          (isDeleted || isCancelled) && "opacity-75"
        )}
      >
        {/* Merchant + amount headline */}
        <div className="flex items-start justify-between gap-3">
          <DisplayField
            label={t("chat.parseCard.merchant")}
            confident={!lowConf(draft.confidence.merchant)}
            display={
              <span className="font-serif text-lg text-ink lowercase-title">
                {draft.merchant}
              </span>
            }
          />
          <DisplayField
            label={t("chat.parseCard.amount")}
            confident={!lowConf(draft.confidence.amount)}
            display={
              <span className="font-serif text-lg tabular text-ink">
                {formatMoney(draft.amountCents)}
              </span>
            }
          />
        </div>

        {/* Meta rows */}
        <div className="mt-3 flex flex-col gap-2 border-t border-hairline pt-3">
          <MetaRow
            icon={<Calendar className="h-3.5 w-3.5" />}
            label={t("chat.parseCard.date")}
            confident={!lowConf(draft.confidence.date)}
            displayValue={formatShortDate(draft.date)}
          />
          <MetaRow
            icon={<CreditCard className="h-3.5 w-3.5" />}
            label={t("chat.parseCard.card")}
            confident={!lowConf(draft.confidence.card)}
            displayValue={cardDisplay}
          />
          <MetaRow
            icon={<Tag className="h-3.5 w-3.5" />}
            label={t("chat.parseCard.category")}
            confident={!lowConf(draft.confidence.category)}
            displayValue={catLabel(draft.category)}
          />
        </div>

        {/* Action / badge area — terminal states get a badge; the fresh
            uncommitted state gets the action buttons. */}
        {isLogged && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-moss-deep">
            <Check className="h-3.5 w-3.5" />
            <span>{t("chat.parseCard.logged")}</span>
          </div>
        )}
        {isDeleted && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <Trash2 className="h-3.5 w-3.5" />
            <span>{t("chat.parseCard.deleted")}</span>
          </div>
        )}
        {isCancelled && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>{t("chat.parseCard.notSaved")}</span>
          </div>
        )}
        {isPending && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>{t("chat.parseCard.queued")}</span>
          </div>
        )}
        {!committed && !frozen && !pendingSync && (
          <>
            <div className="mt-4 flex flex-col gap-2">
              <button
                type="button"
                onClick={() => onConfirm(draft)}
                className="h-11 w-full rounded-2xl bg-moss text-[0.95rem] font-medium text-surface transition-colors hover:bg-moss-deep"
              >
                {t("chat.parseCard.looksRight")}
              </button>
              <button
                type="button"
                onClick={onFix}
                className="h-10 w-full rounded-2xl border border-hairline text-[0.9rem] text-ink transition-colors hover:bg-sunken/60"
              >
                {t("chat.parseCard.letMeFixIt")}
              </button>
            </div>
            <p className="mt-3 text-center text-[0.72rem] text-ink-tertiary">
              {t("chat.parseCard.orJustTell")}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/* ─── Field primitives ──────────────────────────────────────────── */

interface DisplayFieldProps {
  label: string;
  confident: boolean;
  display: React.ReactNode;
}

function DisplayField({ label, confident, display }: DisplayFieldProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[0.65rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      <span className="inline-flex items-center gap-1.5">
        {display}
        {!confident && <LowConfidenceGlyph />}
      </span>
    </div>
  );
}

interface MetaRowProps {
  icon: React.ReactNode;
  label: string;
  displayValue: string;
  confident: boolean;
}

function MetaRow({ icon, label, displayValue, confident }: MetaRowProps) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        {icon}
        <span>{label}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="text-[0.9rem] tabular text-ink">{displayValue}</span>
        {!confident && <LowConfidenceGlyph />}
      </div>
    </div>
  );
}

/** Low-confidence cue — surfaces "double-check this" without offering an
 * inline edit affordance. The user tap-fixes via the sheet. */
function LowConfidenceGlyph() {
  const { t } = useTranslation();
  return (
    <span
      className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-warn-wash text-warn"
      aria-label={t("chat.parseCard.doubleCheckField")}
    >
      <AlertCircle className="h-3 w-3 stroke-[2.2]" />
    </span>
  );
}
