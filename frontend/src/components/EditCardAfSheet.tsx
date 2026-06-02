import { useEffect, useMemo, useState } from "react";
import { Calendar } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { BottomSheet } from "@/components/BottomSheet";
import { patchCard } from "@/lib/cardsApi";
import { currencySymbol } from "@/lib/format";
import { ledger } from "@/lib/ledger";
import { type Card } from "@/lib/fixtures";

interface EditCardAfSheetProps {
  open: boolean;
  card: Card | null;
  /**
   * The companion AF subscription's next_billing_date (ISO YYYY-MM-DD).
   * Day 19b — sourced from `GET /subscriptions?include_card_af=true`
   * + client-side join on `card_id`. When the card has no active AF
   * subscription, pass null and the sheet renders as a "set up AF
   * tracking" entry path.
   */
  afNextDate: string | null;
  /**
   * The companion AF subscription's amount, as displayed string.
   * Falls back to the card's annual_fee snapshot when no active AF
   * subscription exists (re-enable path). Both should always agree
   * (the cascade keeps them in sync); we prefer the subscription as
   * source of truth when present.
   */
  afAmount: string | null;
  onClose: () => void;
  /**
   * Called after a successful save / cancel so the parent can refresh
   * the AF chip read source. The chip data lives on the subscriptions
   * surface, which the parent owns.
   */
  onSaved: () => void;
}

/**
 * Day 19b — bottom sheet for editing a card's annual-fee tracking.
 *
 * All three actions (amount edit, date edit, stop tracking) hit
 * `PATCH /cards/{id}` only — never `PATCH /subscriptions/{id}` — so
 * the server's `update_card_af` RPC owns the cascade onto the
 * companion AF subscription. Keeps `EditSubscriptionSheet`'s
 * pause/cancel/card-reassign affordances (which don't apply to AFs)
 * out of an AF-shaped flow.
 *
 * Source-of-truth rule (DESIGN.md §6.5): `cards.annual_fee` is the
 * canonical amount; the subscription's `amount` mirrors it via the
 * cascade. The amount field here writes to `cards.annual_fee`; the
 * cron auto-log reads the mirrored subscription field next year.
 */
export function EditCardAfSheet({
  open,
  card,
  afNextDate,
  afAmount,
  onClose,
  onSaved,
}: EditCardAfSheetProps) {
  const { t } = useTranslation();
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !card) return;
    // Prefer the subscription's amount when active (post-cascade truth);
    // fall back to the card's annual_fee snapshot for the re-enable
    // case where no active AF sub exists.
    setAmount(afAmount ?? card.annualFee ?? "");
    setDate(afNextDate ?? "");
    setError(null);
  }, [open, card, afAmount, afNextDate]);

  const priorAmount = afAmount ?? card?.annualFee ?? "";
  const priorDate = afNextDate ?? "";

  const trimmedAmount = amount.trim();
  const trimmedDate = date.trim();

  const amountChanged = trimmedAmount !== priorAmount;
  const dateChanged = trimmedDate !== priorDate;
  const dirty = amountChanged || dateChanged;

  const parsedAmount = parseFloat(trimmedAmount);
  const amountValid =
    trimmedAmount === "" ||
    (Number.isFinite(parsedAmount) && parsedAmount >= 0);

  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const dateValid = trimmedDate === "" || trimmedDate >= today;

  const valid = amountValid && dateValid;

  if (!card) {
    return (
      <BottomSheet open={open} onClose={onClose} desktopVariant="side">
        {null}
      </BottomSheet>
    );
  }

  const save = async (): Promise<void> => {
    if (!dirty || !valid || saving) return;
    setSaving(true);
    setError(null);
    const body: Parameters<typeof patchCard>[1] = {};
    if (amountChanged) {
      body.annual_fee = trimmedAmount === "" ? null : trimmedAmount;
    }
    if (dateChanged) {
      body.next_annual_fee_date = trimmedDate === "" ? null : trimmedDate;
    }
    try {
      const updated = await patchCard(card.id, body);
      // Mirror the new annual_fee onto the local fixture-shaped card.
      if (amountChanged) {
        void ledger.updateCard(card.id, {
          annualFee: updated.annual_fee,
        });
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  const stopTracking = async (): Promise<void> => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await patchCard(card.id, { next_annual_fee_date: null });
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  const isTracking = afNextDate !== null;

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      ariaLabel={t("editCard.af.ariaLabel")}
      desktopVariant="side"
    >
      <h2 className="font-serif text-xl text-ink lowercase-title">
        {isTracking ? t("editCard.af.titleEdit") : t("editCard.af.titleTrack")}
      </h2>
      <p className="mt-1 text-[0.78rem] text-ink-tertiary">
        {card.name} · ···· {card.last4}
      </p>

      <div className="mt-5 flex flex-col gap-4">
        <FieldGroup label={t("editCard.fields.amount")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">{currencySymbol()}</span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="0"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </FieldGroup>

        <FieldGroup label={t("editCard.af.nextRenewal")}>
          <div className="flex items-center gap-2">
            <Calendar className="h-3.5 w-3.5 text-ink-tertiary" />
            <input
              type="date"
              value={date}
              min={today}
              onChange={(e) => setDate(e.target.value)}
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
          <p className="mt-2 text-[0.7rem] text-ink-quaternary">
            {t("editCard.af.autoLogHint")}
          </p>
        </FieldGroup>

        {!amountValid && (
          <p className="text-[0.78rem] text-over">
            {t("editCard.af.errorAmountInvalid")}
          </p>
        )}
        {!dateValid && (
          <p className="text-[0.78rem] text-over">
            {t("editCard.af.errorDateInvalid")}
          </p>
        )}
        {error && <p className="text-[0.78rem] text-over">{error}</p>}
      </div>

      <div className="mt-7 flex flex-col gap-3">
        <Button
          fullWidth
          disabled={!dirty || !valid || saving}
          onClick={() => void save()}
        >
          {saving ? t("editCard.saving") : t("editCard.saveChanges")}
        </Button>
        {isTracking && (
          <button
            type="button"
            onClick={() => void stopTracking()}
            disabled={saving}
            className="self-center text-sm text-over hover:underline underline-offset-4 disabled:opacity-50"
          >
            {t("editCard.af.stopTracking")}
          </button>
        )}
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
