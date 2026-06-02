import { useEffect, useState } from "react";
import { ChevronDown, CreditCard } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { BottomSheet } from "@/components/BottomSheet";
import { MultipliersEditor } from "@/components/MultipliersEditor";
import { currencySymbol } from "@/lib/format";
import { ledger } from "@/lib/ledger";
import { type Card, type CardMultiplier, type CardProgram } from "@/lib/fixtures";
import { cn } from "@/lib/utils";

interface EditCardSheetProps {
  open: boolean;
  card: Card | null;
  onClose: () => void;
  /** Called when user taps delete; parent triggers the on-row undo timer. */
  onRequestDelete: (card: Card) => void;
}

/** Programs the user can pick from in the picker — matches the local Card enum. */
const PROGRAM_OPTIONS: { value: CardProgram; label: string }[] = [
  { value: "UR", label: "Chase UR" },
  { value: "MR", label: "Amex MR" },
  { value: "Bilt", label: "Bilt" },
  { value: "ThankYou", label: "Citi ThankYou" },
  { value: "Cash", label: "Cash / Other" },
];

/**
 * Edit a card's mutable fields: name, color, program, multipliers, annual fee.
 * Mirrors `EditTransactionSheet` shape so the two surfaces feel identical.
 *
 * Identity fields (issuer, network, last_four) are intentionally read-only —
 * the backend doesn't accept patches for them (DESIGN.md §8.1). To change
 * identity, the user deletes and re-adds via chat.
 */
export function EditCardSheet({
  open,
  card,
  onClose,
  onRequestDelete,
}: EditCardSheetProps) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [color, setColor] = useState("");
  const [program, setProgram] = useState<CardProgram>("Cash");
  const [annualFee, setAnnualFee] = useState("");
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [pickerOpen, setPickerOpen] = useState<null | "program">(null);

  useEffect(() => {
    if (!card) return;
    setName(card.name);
    setColor(card.color ?? "");
    setProgram(card.program ?? "Cash");
    setAnnualFee(card.annualFee ?? "");
    const m: Record<string, number> = {};
    for (const row of card.multipliers ?? []) m[row.label] = row.factor;
    setMultipliers(m);
  }, [card]);

  if (!card) {
    return <BottomSheet open={open} onClose={onClose} desktopVariant="side">{null}</BottomSheet>;
  }

  const trimmedColor = color.trim();
  const trimmedFee = annualFee.trim();
  const trimmedName = name.trim();

  const priorMult: Record<string, number> = {};
  for (const row of card.multipliers ?? []) priorMult[row.label] = row.factor;

  const multipliersDirty = !mapsEqual(multipliers, priorMult);
  const dirty =
    trimmedName !== card.name ||
    trimmedColor !== (card.color ?? "") ||
    program !== (card.program ?? "Cash") ||
    trimmedFee !== (card.annualFee ?? "") ||
    multipliersDirty;

  const feeValid =
    trimmedFee === "" ||
    (Number.isFinite(parseFloat(trimmedFee)) && parseFloat(trimmedFee) >= 0);
  const valid = trimmedName.length > 0 && feeValid;

  const save = () => {
    if (!dirty || !valid) return;
    const patch: Parameters<typeof ledger.updateCard>[1] = {};
    if (trimmedName !== card.name) patch.name = trimmedName;
    if (trimmedColor !== (card.color ?? "")) {
      patch.color = trimmedColor === "" ? null : trimmedColor;
    }
    if (program !== (card.program ?? "Cash")) patch.program = program;
    if (trimmedFee !== (card.annualFee ?? "")) {
      patch.annualFee = trimmedFee === "" ? null : trimmedFee;
    }
    if (multipliersDirty) {
      const next: CardMultiplier[] = Object.entries(multipliers)
        .map(([label, factor]) => ({ label, factor }))
        .sort((a, b) => b.factor - a.factor);
      patch.multipliers = next;
    }
    void ledger.updateCard(card.id, patch);
    onClose();
  };

  const programLabel =
    PROGRAM_OPTIONS.find((o) => o.value === program)?.label ?? "Cash / Other";

  return (
    <BottomSheet open={open} onClose={onClose} ariaLabel={t("editCard.ariaLabel")} desktopVariant="side">
      <h2 className="font-serif text-xl text-ink lowercase-title">{t("editCard.title")}</h2>

      <div className="mt-5 flex flex-col gap-4">
        <FieldGroup label={t("editCard.fields.name")}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full bg-transparent text-[0.95rem] text-ink focus:outline-none"
          />
        </FieldGroup>

        <FieldGroup label={t("editCard.fields.last4")}>
          <div className="flex items-center justify-between">
            <span className="tabular text-[0.95rem] text-ink-tertiary">
              ···· {card.last4 || "—"}
            </span>
            <span className="text-[0.7rem] text-ink-quaternary">
              {t("editCard.fields.last4Hint")}
            </span>
          </div>
        </FieldGroup>

        <FieldButton
          label={t("editCard.fields.program")}
          icon={<CreditCard className="h-3.5 w-3.5" />}
          value={programLabel}
          onClick={() => setPickerOpen("program")}
        />

        <FieldGroup label={t("editCard.fields.color")}>
          <div className="flex items-center gap-3">
            <input
              type="color"
              value={trimmedColor || "#8A8377"}
              onChange={(e) => setColor(e.target.value)}
              aria-label={t("editCard.fields.colorAriaLabel")}
              className="h-7 w-10 cursor-pointer rounded-lg border border-hairline bg-transparent"
            />
            <input
              value={color}
              onChange={(e) => setColor(e.target.value)}
              placeholder="#8A8377"
              className="flex-1 bg-transparent text-[0.95rem] text-ink focus:outline-none"
            />
          </div>
        </FieldGroup>

        <FieldGroup label={t("editCard.fields.annualFee")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">{currencySymbol()}</span>
            <input
              value={annualFee}
              onChange={(e) => setAnnualFee(e.target.value)}
              inputMode="decimal"
              placeholder="0"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </FieldGroup>

        <div className="rounded-2xl border border-hairline bg-surface px-4 py-3">
          <MultipliersEditor
            multipliers={multipliers}
            onMultipliers={setMultipliers}
          />
        </div>
      </div>

      <div className="mt-7 flex flex-col gap-3">
        <Button fullWidth disabled={!dirty || !valid} onClick={save}>
          {t("editCard.saveChanges")}
        </Button>
        <button
          type="button"
          onClick={() => onRequestDelete(card)}
          className="self-center text-sm text-over hover:underline underline-offset-4"
        >
          {t("editCard.deleteCard")}
        </button>
      </div>

      <BottomSheet
        open={pickerOpen !== null}
        onClose={() => setPickerOpen(null)}
        ariaLabel={t("editCard.programPicker.ariaLabel")}
      >
        <h3 className="font-serif text-lg text-ink lowercase-title">{t("editCard.programPicker.title")}</h3>
        <ul className="mt-3 flex flex-col">
          {PROGRAM_OPTIONS.map((o) => (
            <li key={o.value}>
              <PickerRow
                active={o.value === program}
                label={o.label}
                onClick={() => {
                  setProgram(o.value);
                  setPickerOpen(null);
                }}
              />
            </li>
          ))}
        </ul>
      </BottomSheet>
    </BottomSheet>
  );
}

function mapsEqual(a: Record<string, number>, b: Record<string, number>): boolean {
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    if (b[k] !== a[k]) return false;
  }
  return true;
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
  onClick,
}: {
  active: boolean;
  label: string;
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
      <span className="text-[0.95rem] text-ink">{label}</span>
      {active && <span className="h-2 w-2 rounded-full bg-moss" />}
    </button>
  );
}
