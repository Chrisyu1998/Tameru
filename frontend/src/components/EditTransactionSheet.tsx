import { useEffect, useState } from "react";
import { Calendar, ChevronDown, CreditCard, Tag } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { BottomSheet } from "@/components/BottomSheet";
import { CATEGORIES, useCategoryLabel, type Category } from "@/lib/categories";
import { currencySymbol } from "@/lib/format";
import { ledger } from "@/lib/ledger";
import { type Card, type Transaction } from "@/lib/fixtures";
import { cn } from "@/lib/utils";

interface EditTransactionSheetProps {
  open: boolean;
  transaction: Transaction | null;
  cards: Card[];
  onClose: () => void;
  /** Called when user taps delete; parent handles the undo toast. */
  onRequestDelete: (tx: Transaction) => void;
  /**
   * Optional override for the "save" action. The default (used by the
   * dashboard / breakdown edit flows) PATCHes the row in the ledger. The
   * chat "fix-draft" flow supplies its own — see pages/chat.tsx — so the
   * sheet edits the in-flight parse draft instead of trying to mutate a
   * row that doesn't exist server-side yet.
   */
  onSave?: (tx: Transaction, patch: Partial<Transaction>) => void;
}

export function EditTransactionSheet({
  open,
  transaction,
  cards,
  onClose,
  onRequestDelete,
  onSave,
}: EditTransactionSheetProps) {
  const { t } = useTranslation();
  const catLabel = useCategoryLabel();
  const [merchant, setMerchant] = useState("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState("");
  const [cardId, setCardId] = useState("");
  const [category, setCategory] = useState<Category>("Other");
  const [pickerOpen, setPickerOpen] = useState<null | "card" | "category">(null);

  useEffect(() => {
    if (!transaction) return;
    setMerchant(transaction.merchant);
    setAmount((transaction.amountCents / 100).toString());
    setDate(transaction.date);
    setCardId(transaction.cardId);
    setCategory(transaction.category);
  }, [transaction]);

  if (!transaction) {
    return <BottomSheet open={open} onClose={onClose} desktopVariant="side">{null}</BottomSheet>;
  }

  const dirty =
    merchant !== transaction.merchant ||
    Math.round(parseFloat(amount || "0") * 100) !== transaction.amountCents ||
    date !== transaction.date ||
    cardId !== transaction.cardId ||
    category !== transaction.category;

  const valid =
    merchant.trim().length > 0 &&
    !isNaN(parseFloat(amount)) &&
    parseFloat(amount) > 0 &&
    date.length > 0;

  const save = () => {
    if (!dirty || !valid) return;
    const patch: Partial<Transaction> = {
      merchant: merchant.trim(),
      amountCents: Math.round(parseFloat(amount) * 100),
      date,
      cardId,
      category,
    };
    if (onSave) {
      onSave(transaction, patch);
    } else {
      void ledger.updateTransaction(transaction.id, patch);
    }
    onClose();
  };

  const selectedCard = cards.find((c) => c.id === cardId);

  return (
    <BottomSheet open={open} onClose={onClose} ariaLabel={t("editTransaction.ariaLabel")} desktopVariant="side">
      <h2 className="font-serif text-xl text-ink lowercase-title">{t("editTransaction.title")}</h2>

      <div className="mt-5 flex flex-col gap-4">
        <FieldGroup label={t("editTransaction.fields.merchant")}>
          <input
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
            className="w-full bg-transparent text-[0.95rem] text-ink focus:outline-none"
          />
        </FieldGroup>

        <FieldGroup label={t("editTransaction.fields.amount")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">{currencySymbol()}</span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </FieldGroup>

        <FieldGroup label={t("editTransaction.fields.date")}>
          <div className="flex items-center gap-2">
            <Calendar className="h-3.5 w-3.5 text-ink-tertiary" />
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </FieldGroup>

        <FieldButton
          label={t("editTransaction.fields.card")}
          icon={<CreditCard className="h-3.5 w-3.5" />}
          value={selectedCard ? `${selectedCard.name} · ${selectedCard.last4}` : t("editTransaction.card.other")}
          onClick={() => setPickerOpen("card")}
        />

        <FieldButton
          label={t("editTransaction.fields.category")}
          icon={<Tag className="h-3.5 w-3.5" />}
          value={catLabel(category)}
          onClick={() => setPickerOpen("category")}
        />
      </div>

      <div className="mt-7 flex flex-col gap-3">
        <Button fullWidth disabled={!dirty || !valid} onClick={save}>
          {t("editTransaction.saveChanges")}
        </Button>
        <button
          type="button"
          onClick={() => onRequestDelete(transaction)}
          className="self-center text-sm text-over hover:underline underline-offset-4"
        >
          {t("editTransaction.deleteTransaction")}
        </button>
      </div>

      {/* Picker sheet (card or category) */}
      <BottomSheet
        open={pickerOpen !== null}
        onClose={() => setPickerOpen(null)}
        ariaLabel={pickerOpen === "card" ? t("editTransaction.cardPicker.ariaLabel") : t("editTransaction.categoryPicker.ariaLabel")}
      >
        {pickerOpen === "card" && (
          <>
            <h3 className="font-serif text-lg text-ink lowercase-title">{t("editTransaction.cardPicker.title")}</h3>
            <ul className="mt-3 flex flex-col">
              <li>
                <PickerRow
                  active={cardId === ""}
                  label={t("editTransaction.card.otherCash")}
                  sub={t("editTransaction.card.otherCashSub")}
                  onClick={() => {
                    setCardId("");
                    setPickerOpen(null);
                  }}
                />
              </li>
              {cards.map((c) => (
                <li key={c.id}>
                  <PickerRow
                    active={c.id === cardId}
                    label={c.name}
                    sub={`···· ${c.last4}`}
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
        {pickerOpen === "category" && (
          <>
            <h3 className="font-serif text-lg text-ink lowercase-title">{t("editTransaction.categoryPicker.title")}</h3>
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
}: {
  label: string;
  icon: React.ReactNode;
  value: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between rounded-2xl border border-hairline bg-surface px-4 py-3 text-left transition-colors hover:bg-elevated"
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
        active && "bg-moss-wash/40"
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
