import { useState } from "react";
import { Check, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  ISSUERS,
  ISSUER_LABELS,
  type CardIssuer,
  type CardNetwork,
  type CardProgram,
} from "@/lib/cardsApi";
import type { CardParseDraft } from "@/lib/chat";
import { cn } from "@/lib/utils";

/**
 * Chat-side card parse card — Day 14b. Sister of `ParseCard` (transactions)
 * with the card-specific field set. The agent emits a `propose_card` tool
 * call carrying the web_search lookup result; this component renders the
 * editable preview the user reviews before tapping "looks right" → POST
 * /cards/confirm.
 *
 * UX contract:
 *   - Card name + issuer/network chip headline.
 *   - Required last-4 input (4 digits) — confirm stays disabled otherwise.
 *   - Multipliers + annual fee shown read-only as a quick sanity check;
 *     edits live on the cards page (PATCH /cards/{id}) post-commit so we
 *     don't grow this card into a second AddCardStep.
 *   - When the lookup couldn't determine `issuer` or `network`, we render
 *     amber "select…" selects and the confirm button stays disabled until
 *     both are picked — matching the AddCardStep posture (the issuer is
 *     the uniqueness tiebreaker, so silently defaulting to "other" would
 *     produce wrong identity metadata).
 */

const NETWORKS: { value: CardNetwork; label: string }[] = [
  { value: "visa", label: "Visa" },
  { value: "mastercard", label: "Mastercard" },
  { value: "amex", label: "Amex" },
  { value: "discover", label: "Discover" },
  { value: "jcb", label: "JCB" },
  { value: "diners", label: "Diners Club" },
  { value: "other", label: "Other" },
];

interface CardParseCardProps {
  preface?: string;
  draft: CardParseDraft;
  /** When set, the card is locked into "added" state. */
  committed: boolean;
  /**
   * Lifecycle of the committed card row (only meaningful with
   * `committed=true`). `'active'` → `added.`; `'deleted'` →
   * `deleted.` — the user closed this card from their wallet. Mirrors
   * `ParseCardProps.committedState` for transactions.
   */
  committedState?: "active" | "deleted";
  /**
   * `true` for cards rehydrated from history — read-only historical
   * artifact. Disables every input and surfaces a "not saved." badge if
   * the original confirm never happened. See ParseCard for the parallel.
   */
  frozen?: boolean;
  /**
   * `true` between an offline "looks right" tap and drain. See
   * `ParseCardProps.pendingSync` — same semantics here.
   */
  pendingSync?: boolean;
  onConfirm: (draft: CardParseDraft) => void;
}

export function CardParseCard({
  preface,
  draft,
  committed,
  committedState,
  frozen,
  pendingSync,
  onConfirm,
}: CardParseCardProps) {
  const { t } = useTranslation();
  const [local, setLocal] = useState<CardParseDraft>(draft);
  const lastFourValid = /^\d{4}$/.test(local.lastFour);
  const issuerUnresolved = local.issuer === null;
  const networkUnresolved = local.network === null;
  const canConfirm =
    lastFourValid &&
    !issuerUnresolved &&
    !networkUnresolved &&
    !committed &&
    !frozen &&
    !pendingSync;

  const isDeleted = committed && committedState === "deleted";
  const isAdded = committed && !isDeleted;
  const isCancelled = !committed && !!frozen && !pendingSync;
  const isPending = !committed && !!pendingSync;

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
          (isDeleted || isCancelled) && "opacity-75",
        )}
      >
        {/* Card headline */}
        <div className="flex flex-col gap-1">
          <span className="font-serif text-lg text-ink lowercase-title">
            {local.name}
          </span>
          <span className="text-xs text-ink-tertiary tabular">
            {local.issuer ? ISSUER_LABELS[local.issuer] : "—"} ·{" "}
            {local.network ?? "—"}
            {local.lastFour ? ` · ···· ${local.lastFour}` : ""}
          </span>
        </div>

        {(issuerUnresolved || networkUnresolved) && !committed && !frozen && !pendingSync && (
          <p className="mt-2 text-xs text-warn">
            {t("chat.cardParseCard.lookupWarning")}
            {issuerUnresolved && networkUnresolved
              ? t("chat.cardParseCard.lookupWarningIssuerAndNetwork")
              : issuerUnresolved
                ? t("chat.cardParseCard.lookupWarningIssuer")
                : t("chat.cardParseCard.lookupWarningNetwork")}{" "}
            {t("chat.cardParseCard.lookupWarningTail")}
          </p>
        )}

        {/* Editable fields — surfaced inline so the user doesn't have to
            pop a sheet just to fill last-4. Issuer/network selects only
            render when unresolved (or while editing pre-confirm) to keep
            the card visually quiet on the happy path. Rehydrated cards
            (`frozen`) skip this block entirely — they're historical. */}
        {!committed && !frozen && !pendingSync && (
          <>
            <div className="mt-4 grid grid-cols-2 gap-3 border-t border-hairline pt-3">
              <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                {t("chat.cardParseCard.last4")}
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="\d{4}"
                  maxLength={4}
                  value={local.lastFour}
                  onChange={(e) =>
                    setLocal({
                      ...local,
                      lastFour: e.target.value.replace(/\D/g, "").slice(0, 4),
                    })
                  }
                  placeholder="1234"
                  className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink placeholder:text-ink-quaternary focus:outline-none"
                />
              </label>
              <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                {t("chat.cardParseCard.annualFee")}
                <input
                  type="text"
                  inputMode="decimal"
                  value={local.annualFee ?? ""}
                  onChange={(e) =>
                    setLocal({
                      ...local,
                      annualFee:
                        e.target.value.replace(/[^\d.]/g, "") || null,
                    })
                  }
                  placeholder="0"
                  className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink placeholder:text-ink-quaternary focus:outline-none"
                />
              </label>
            </div>

            {/* Day 19b — next AF renewal date. Only when the card has a
                non-zero annual fee (no fee = nothing to auto-log).
                Optional; an empty value means "don't track for now,"
                and the user can enable it later from the cards-page
                "track AF" chip. When set, `POST /cards/confirm` fires
                the atomic dual-write via `insert_card_with_af`. */}
            {hasPositiveAnnualFee(local.annualFee) && (
              <div className="mt-3">
                <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                  {t("chat.cardParseCard.nextRenewal")}
                  <div className="mt-1 flex items-center gap-2">
                    <input
                      type="date"
                      value={local.nextAnnualFeeDate ?? ""}
                      min={todayIso()}
                      placeholder={defaultRenewalIso()}
                      onChange={(e) =>
                        setLocal({
                          ...local,
                          nextAnnualFeeDate: e.target.value || null,
                        })
                      }
                      className="flex-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm tabular text-ink placeholder:text-ink-quaternary focus:outline-none"
                    />
                    {local.nextAnnualFeeDate && (
                      <button
                        type="button"
                        onClick={() =>
                          setLocal({ ...local, nextAnnualFeeDate: null })
                        }
                        aria-label={t("chat.cardParseCard.clearRenewalDate")}
                        className="rounded-md px-1.5 py-1 text-ink-tertiary hover:bg-elevated"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  {local.nextAnnualFeeDate && (
                    <p className="mt-1 text-[0.65rem] normal-case text-ink-quaternary">
                      {t("chat.cardParseCard.autoLog")}
                    </p>
                  )}
                </label>
              </div>
            )}

            {(issuerUnresolved || networkUnresolved || local.needsManual) && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                  {t("chat.cardParseCard.issuer")}
                  <select
                    value={local.issuer ?? ""}
                    onChange={(e) =>
                      setLocal({
                        ...local,
                        issuer: e.target.value as CardIssuer,
                      })
                    }
                    className={cn(
                      "mt-1 rounded-lg border bg-surface px-2 py-1 text-sm text-ink focus:outline-none",
                      issuerUnresolved
                        ? "border-warn ring-1 ring-warn/30"
                        : "border-hairline",
                    )}
                  >
                    {issuerUnresolved && (
                      <option value="" disabled>
                        {t("chat.cardParseCard.selectPlaceholder")}
                      </option>
                    )}
                    {ISSUERS.map((i) => (
                      <option key={i} value={i}>
                        {ISSUER_LABELS[i]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                  {t("chat.cardParseCard.network")}
                  <select
                    value={local.network ?? ""}
                    onChange={(e) =>
                      setLocal({
                        ...local,
                        network: e.target.value as CardNetwork,
                      })
                    }
                    className={cn(
                      "mt-1 rounded-lg border bg-surface px-2 py-1 text-sm text-ink focus:outline-none",
                      networkUnresolved
                        ? "border-warn ring-1 ring-warn/30"
                        : "border-hairline",
                    )}
                  >
                    {networkUnresolved && (
                      <option value="" disabled>
                        {t("chat.cardParseCard.selectPlaceholder")}
                      </option>
                    )}
                    {NETWORKS.map((n) => (
                      <option key={n.value} value={n.value}>
                        {n.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </>
        )}

        {/* Reward preview — read-only here; the cards page is where the
            user fine-tunes after the card lands. US cards show category
            multipliers; JP/TW cards (Tier 3, DESIGN.md §6.6) show the base
            earn rate + rewards-currency label. */}
        {(local.baseRewardRate || local.rewardsCurrency) ? (
          <div className="mt-3 flex flex-wrap gap-1.5 border-t border-hairline pt-3">
            {local.baseRewardRate && (
              <span className="rounded-full bg-sunken px-2 py-0.5 text-[0.72rem] text-ink-secondary">
                {local.baseRewardRate}% base
              </span>
            )}
            {local.rewardsCurrency && (
              <span className="rounded-full bg-sunken px-2 py-0.5 text-[0.72rem] text-ink-secondary">
                {local.rewardsCurrency}
              </span>
            )}
          </div>
        ) : (
          Object.keys(local.multipliers).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5 border-t border-hairline pt-3">
              {Object.entries(local.multipliers).map(([cat, mult]) => (
                <span
                  key={cat}
                  className="rounded-full bg-sunken px-2 py-0.5 text-[0.72rem] text-ink-secondary"
                >
                  {mult}× {cat.toLowerCase()}
                </span>
              ))}
            </div>
          )
        )}

        {local.sourceUrls.length > 0 && !committed && !frozen && !pendingSync && (
          <div className="mt-3 border-t border-hairline pt-2 text-[0.7rem] text-ink-quaternary">
            {t("chat.cardParseCard.sources")}&nbsp;
            {local.sourceUrls.slice(0, 3).map((u, i) => (
              <span key={u}>
                {i > 0 ? ", " : ""}
                <a
                  href={u}
                  target="_blank"
                  rel="noreferrer"
                  className="underline decoration-dotted underline-offset-2 hover:text-ink-tertiary"
                >
                  {_safeHostname(u)}
                </a>
              </span>
            ))}
          </div>
        )}

        {/* Action / badge area — terminal states get a badge, fresh
            uncommitted state gets the action button. Three terminal
            badge variants mirror ParseCard's transaction surface. */}
        {isAdded && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-moss-deep">
            <Check className="h-3.5 w-3.5" />
            <span>{t("chat.cardParseCard.added")}</span>
          </div>
        )}
        {isDeleted && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <Trash2 className="h-3.5 w-3.5" />
            <span>{t("chat.cardParseCard.deleted")}</span>
          </div>
        )}
        {isCancelled && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>{t("chat.cardParseCard.notSaved")}</span>
          </div>
        )}
        {isPending && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>{t("chat.cardParseCard.queued")}</span>
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
              {t("chat.cardParseCard.looksRight")}
            </button>
            {!lastFourValid && (
              <p className="mt-2 text-center text-[0.72rem] text-ink-tertiary">
                {t("chat.cardParseCard.enterLast4")}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Hostname extraction that doesn't throw on malformed URLs. */
function _safeHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * True when the parse card's `annualFee` field is a positive number.
 * Day 19b gates the "next renewal" row on this — a $0 / null fee
 * means there's nothing for the cron to auto-log, so capturing a
 * renewal date would be pointless and the confirm endpoint would
 * 422 anyway.
 */
function hasPositiveAnnualFee(value: string | null): boolean {
  if (value === null || value === "") return false;
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0;
}

/** Today as YYYY-MM-DD — `min` attribute on the renewal date input. */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Default suggestion = today + 365 days as YYYY-MM-DD. Used as a
 * placeholder hint, not as an auto-fill — the user has to actively
 * pick a date for AF tracking to engage (the server intentionally
 * does NOT auto-default; see Day 19b prompt's "Design decisions").
 */
function defaultRenewalIso(): string {
  const d = new Date();
  d.setDate(d.getDate() + 365);
  return d.toISOString().slice(0, 10);
}
