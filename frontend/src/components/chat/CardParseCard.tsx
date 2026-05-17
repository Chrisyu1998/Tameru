import { useState } from "react";
import { Check, Trash2 } from "lucide-react";
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
  onConfirm: (draft: CardParseDraft) => void;
}

export function CardParseCard({
  preface,
  draft,
  committed,
  committedState,
  frozen,
  onConfirm,
}: CardParseCardProps) {
  const [local, setLocal] = useState<CardParseDraft>(draft);
  const lastFourValid = /^\d{4}$/.test(local.lastFour);
  const issuerUnresolved = local.issuer === null;
  const networkUnresolved = local.network === null;
  const canConfirm =
    lastFourValid &&
    !issuerUnresolved &&
    !networkUnresolved &&
    !committed &&
    !frozen;

  const isDeleted = committed && committedState === "deleted";
  const isAdded = committed && !isDeleted;
  const isCancelled = !committed && !!frozen;

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

        {(issuerUnresolved || networkUnresolved) && !committed && !frozen && (
          <p className="mt-2 text-xs text-warn">
            lookup couldn't determine
            {issuerUnresolved && networkUnresolved
              ? " issuer or network"
              : issuerUnresolved
                ? " the issuing bank"
                : " the card network"}{" "}
            — pick below to continue.
          </p>
        )}

        {/* Editable fields — surfaced inline so the user doesn't have to
            pop a sheet just to fill last-4. Issuer/network selects only
            render when unresolved (or while editing pre-confirm) to keep
            the card visually quiet on the happy path. Rehydrated cards
            (`frozen`) skip this block entirely — they're historical. */}
        {!committed && !frozen && (
          <>
            <div className="mt-4 grid grid-cols-2 gap-3 border-t border-hairline pt-3">
              <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                last 4
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
                annual fee
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

            {(issuerUnresolved || networkUnresolved || local.needsManual) && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="flex flex-col text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
                  issuer
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
                        select…
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
                  network
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
                        select…
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

        {/* Multipliers preview — read-only here; the cards page is where
            the user fine-tunes them after the card lands. */}
        {Object.keys(local.multipliers).length > 0 && (
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
        )}

        {local.sourceUrls.length > 0 && !committed && !frozen && (
          <div className="mt-3 border-t border-hairline pt-2 text-[0.7rem] text-ink-quaternary">
            sources:&nbsp;
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
            <span>added.</span>
          </div>
        )}
        {isDeleted && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <Trash2 className="h-3.5 w-3.5" />
            <span>deleted.</span>
          </div>
        )}
        {isCancelled && (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-ink-tertiary">
            <span>not saved.</span>
          </div>
        )}
        {!committed && !frozen && (
          <>
            <button
              type="button"
              onClick={() => onConfirm(local)}
              disabled={!canConfirm}
              className="mt-4 h-11 w-full rounded-2xl bg-moss text-[0.95rem] font-medium text-surface transition-colors hover:bg-moss-deep disabled:cursor-not-allowed disabled:opacity-50"
            >
              looks right
            </button>
            {!lastFourValid && (
              <p className="mt-2 text-center text-[0.72rem] text-ink-tertiary">
                enter the last 4 digits to continue.
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
