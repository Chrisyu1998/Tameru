import { useEffect, useMemo, useState } from "react";
import { ChevronDown, CreditCard, Pause, Play, Tag, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { BottomSheet } from "@/components/BottomSheet";
import { Pill } from "@/components/Pill";
import { CATEGORIES, useCategoryLabel, type Category } from "@/lib/categories";
import {
  cancelSubscription,
  formatFrequency,
  pauseSubscription,
  reassignSubscriptionCard,
  resumeSubscription,
  updateSubscription,
  type SubscriptionRow,
} from "@/lib/subscriptions";
import { type Card } from "@/lib/fixtures";
import { currencySymbol, formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

interface EditSubscriptionSheetProps {
  open: boolean;
  subscription: SubscriptionRow | null;
  cards: Card[];
  onClose: () => void;
}

/**
 * Edit a subscription's mutable fields: name, amount, category, card.
 *
 * `frequency` and `start_date` are immutable post-create (DESIGN.md §8.3).
 * The sheet renders them read-only with a hint to cancel-and-re-add to
 * change billing cadence. Status actions (pause/resume/cancel) live on
 * the same sheet — destructive actions go below the save row so they're
 * not the primary affordance.
 *
 * Mirrors `EditCardSheet` and `EditTransactionSheet`: dirty + valid
 * tracked locally, picker sheets nested for category and card. Wire
 * shape (`PATCH /subscriptions/{id}` with `{ name?, amount?, category?,
 * card_id? }`) is constructed only with the fields the user touched, so
 * an unchanged-value save still no-ops on the backend.
 */
export function EditSubscriptionSheet({
  open,
  subscription,
  cards,
  onClose,
}: EditSubscriptionSheetProps) {
  const { t } = useTranslation();
  const catLabel = useCategoryLabel();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState<Category>("Memberships");
  const [cardId, setCardId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState<null | "card" | "category">(null);

  useEffect(() => {
    if (!subscription) return;
    setName(subscription.name);
    setAmount(subscription.amount);
    setCategory(subscription.category as Category);
    setCardId(subscription.card_id);
  }, [subscription]);

  const cardsById = useMemo(() => {
    const m = new Map<string, Card>();
    for (const c of cards) m.set(c.id, c);
    return m;
  }, [cards]);

  if (!subscription) {
    return (
      <BottomSheet open={open} onClose={onClose} desktopVariant="side">
        {null}
      </BottomSheet>
    );
  }

  const trimmedName = name.trim();
  const trimmedAmount = amount.trim();
  const parsedAmount = parseFloat(trimmedAmount);
  const amountValid =
    trimmedAmount.length > 0 && Number.isFinite(parsedAmount) && parsedAmount > 0;
  const nameValid = trimmedName.length > 0;

  // String-compare amount on the wire so "9.99" → "9.99" doesn't false-
  // positive as dirty. Numeric-compare guards against "9.99" vs "9.990".
  const amountDirty =
    amountValid && parsedAmount !== parseFloat(subscription.amount);
  const dirty =
    trimmedName !== subscription.name ||
    amountDirty ||
    category !== (subscription.category as Category) ||
    cardId !== subscription.card_id;
  const valid = nameValid && amountValid;

  const backingCard = subscription.card_id
    ? cardsById.get(subscription.card_id)
    : null;
  // Backing card was soft-deleted by the §8.3 split-cascade. Selecting a
  // new card (or ACH) is the recovery path; the resume guard server-side
  // requires either before flipping status back to active.
  const needsNewCard = subscription.card_id != null && !backingCard;

  const save = async (): Promise<void> => {
    if (!dirty || !valid) return;
    const patch: {
      name?: string;
      amount?: string;
      category?: string;
      card_id?: string | null;
    } = {};
    if (trimmedName !== subscription.name) patch.name = trimmedName;
    if (amountDirty) patch.amount = parsedAmount.toFixed(2);
    if (category !== (subscription.category as Category)) patch.category = category;
    if (cardId !== subscription.card_id) patch.card_id = cardId;
    await updateSubscription(subscription.id, patch);
    onClose();
  };

  const togglePause = (): void => {
    if (subscription.status === "active") void pauseSubscription(subscription.id);
    else if (subscription.status === "paused") void resumeSubscription(subscription.id);
    onClose();
  };

  const resumeAsAch = (): void => {
    void reassignSubscriptionCard(subscription.id, null).then(() =>
      resumeSubscription(subscription.id),
    );
    onClose();
  };

  const cancel = (): void => {
    void cancelSubscription(subscription.id);
    onClose();
  };

  const selectedCardLabel = cardId
    ? cardsById.get(cardId)
      ? `${cardsById.get(cardId)!.name} · ···· ${cardsById.get(cardId)!.last4 ?? ""}`
      : t("editSubscription.card.needsNewCard")
    : t("editSubscription.card.bankAch");

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      ariaLabel={t("editSubscription.ariaLabel")}
      desktopVariant="side"
    >
      <header>
        <h2 className="font-serif text-xl text-ink lowercase-title">
          {t("editSubscription.title")}
        </h2>
        {subscription.status === "paused" && (
          <Pill tone="neutral" className="mt-2">
            {t("subscriptions.status.paused")}
          </Pill>
        )}
        {subscription.status === "cancelled" && (
          <Pill tone="neutral" className="mt-2">
            {t("subscriptions.status.cancelled")}
          </Pill>
        )}
      </header>

      <div className="mt-5 flex flex-col gap-4">
        <FieldGroup label={t("editSubscription.fields.name")}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={subscription.status === "cancelled"}
            className="w-full bg-transparent text-[0.95rem] text-ink focus:outline-none disabled:text-ink-tertiary"
          />
        </FieldGroup>

        <FieldGroup label={t("editSubscription.fields.amount")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">{currencySymbol()}</span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              disabled={subscription.status === "cancelled"}
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none disabled:text-ink-tertiary"
            />
          </div>
        </FieldGroup>

        <FieldButton
          label={t("editSubscription.fields.category")}
          icon={<Tag className="h-3.5 w-3.5" />}
          value={catLabel(category)}
          onClick={() => setPickerOpen("category")}
          disabled={subscription.status === "cancelled"}
        />

        <FieldButton
          label={needsNewCard ? t("editSubscription.fields.cardClosed") : t("editSubscription.fields.card")}
          icon={<CreditCard className="h-3.5 w-3.5" />}
          value={selectedCardLabel}
          onClick={() => setPickerOpen("card")}
          warn={needsNewCard}
          disabled={subscription.status === "cancelled"}
        />

        <ReadOnlyRow
          label={t("editSubscription.fields.frequency")}
          value={formatFrequency(subscription.frequency)}
        />
        <ReadOnlyRow
          label={subscription.status === "active" ? t("editSubscription.fields.nextBilling") : t("editSubscription.fields.lastBilling")}
          value={formatShortDate(subscription.next_billing_date)}
        />
        <ReadOnlyRow
          label={t("editSubscription.fields.started")}
          value={formatShortDate(subscription.start_date)}
        />

        <p className="text-[0.72rem] text-ink-tertiary">
          {t("editSubscription.cadenceHint")}
        </p>
      </div>

      <div className="mt-7 flex flex-col gap-3">
        {subscription.status !== "cancelled" && (
          <Button fullWidth disabled={!dirty || !valid} onClick={save}>
            {t("editSubscription.saveChanges")}
          </Button>
        )}

        {subscription.status !== "cancelled" && (
          <div className="flex flex-col gap-2">
            {subscription.status === "paused" && needsNewCard ? (
              <button
                type="button"
                onClick={resumeAsAch}
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-[0.95rem] text-ink hover:bg-elevated"
              >
                <Play className="h-4 w-4" /> {t("editSubscription.actions.resumeAsAch")}
              </button>
            ) : (
              <button
                type="button"
                onClick={togglePause}
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-[0.95rem] text-ink hover:bg-elevated"
              >
                {subscription.status === "active" ? (
                  <>
                    <Pause className="h-4 w-4" /> {t("editSubscription.actions.pause")}
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4" /> {t("editSubscription.actions.resume")}
                  </>
                )}
              </button>
            )}
            <button
              type="button"
              onClick={cancel}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl bg-warn-wash/40 text-[0.95rem] text-over hover:bg-warn-wash/70"
            >
              <X className="h-4 w-4" /> {t("editSubscription.actions.cancel")}
            </button>
          </div>
        )}
      </div>

      <BottomSheet
        open={pickerOpen !== null}
        onClose={() => setPickerOpen(null)}
        ariaLabel={pickerOpen === "card" ? t("editSubscription.cardPicker.ariaLabel") : t("editSubscription.categoryPicker.ariaLabel")}
      >
        {pickerOpen === "category" && (
          <>
            <h3 className="font-serif text-lg text-ink lowercase-title">
              {t("editSubscription.categoryPicker.title")}
            </h3>
            <ul className="mt-3 flex flex-col">
              {CATEGORIES.map((c) => (
                <li key={c}>
                  <PickerRow
                    active={c === category}
                    label={catLabel(c)}
                    onClick={() => {
                      setCategory(c);
                      setPickerOpen(null);
                    }}
                  />
                </li>
              ))}
            </ul>
          </>
        )}
        {pickerOpen === "card" && (
          <>
            <h3 className="font-serif text-lg text-ink lowercase-title">
              {t("editSubscription.cardPicker.title")}
            </h3>
            <ul className="mt-3 flex flex-col">
              <li>
                <PickerRow
                  active={cardId === null}
                  label={t("editSubscription.card.bankAch")}
                  sub={t("editSubscription.card.bankAchSub")}
                  onClick={() => {
                    setCardId(null);
                    setPickerOpen(null);
                  }}
                />
              </li>
              {cards.map((c) => (
                <li key={c.id}>
                  <PickerRow
                    active={c.id === cardId}
                    label={c.name}
                    sub={`···· ${c.last4 ?? ""}`}
                    onClick={() => {
                      setCardId(c.id);
                      setPickerOpen(null);
                    }}
                  />
                </li>
              ))}
            </ul>
          </>
        )}
      </BottomSheet>
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

function FieldButton({
  label,
  icon,
  value,
  onClick,
  warn,
  disabled,
}: {
  label: string;
  icon: React.ReactNode;
  value: string;
  onClick: () => void;
  warn?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex w-full items-center justify-between rounded-2xl border border-hairline bg-surface px-4 py-3 text-left transition-colors hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-55",
        warn && "border-warn-wash/60 bg-warn-wash/20",
      )}
    >
      <div className="flex flex-col">
        <span className="text-[0.65rem] uppercase tracking-wider text-ink-tertiary">
          {label}
        </span>
        <span className="mt-1 flex items-center gap-2 text-[0.95rem] text-ink">
          {icon}
          {value}
        </span>
      </div>
      <ChevronDown className="h-4 w-4 text-ink-tertiary" />
    </button>
  );
}

function ReadOnlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded-2xl border border-hairline bg-sunken/30 px-4 py-3">
      <span className="text-[0.65rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      <span className="text-[0.9rem] text-ink-secondary tabular">{value}</span>
    </div>
  );
}

function PickerRow({
  active,
  label,
  sub,
  onClick,
}: {
  active: boolean;
  label: string;
  sub?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center justify-between rounded-xl px-3 py-3 text-left transition-colors hover:bg-sunken/60",
        active && "bg-moss-wash/40",
      )}
    >
      <div className="flex flex-col leading-tight">
        <span className="text-[0.95rem] text-ink">{label}</span>
        {sub && <span className="text-xs text-ink-tertiary tabular">{sub}</span>}
      </div>
      {active && <span className="h-2 w-2 rounded-full bg-moss" />}
    </button>
  );
}
