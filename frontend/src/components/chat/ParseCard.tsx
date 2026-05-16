import { useState } from "react";
import { Calendar, Check, CreditCard, Pencil, Tag } from "lucide-react";
import { cardLabel, type ParseDraft } from "@/lib/chat";
import { CATEGORIES, type Category } from "@/lib/categories";
import { formatMoney, formatShortDate } from "@/lib/format";
import { FIXTURE_CARDS } from "@/lib/fixtures";
import { cn } from "@/lib/utils";

// "Other / Cash" is the always-available choice — represented by an empty
// cardId on the draft, persisted server-side as card_id = NULL. Real cards
// (post-v1, once /cards ships) sit alongside it in the picker.

interface ParseCardProps {
  preface?: string;
  draft: ParseDraft;
  /** When set, the card is locked into "logged" state. */
  committed: boolean;
  onConfirm: (draft: ParseDraft) => void;
  onFix: () => void;
}

/**
 * The primary commit surface.
 * Per spec: looks like the primary action — accent prominent.
 * Lower-confidence fields show a slightly more prominent pencil.
 */
export function ParseCard({
  preface,
  draft,
  committed,
  onConfirm,
  onFix,
}: ParseCardProps) {
  const [local, setLocal] = useState<ParseDraft>(draft);
  const card = cardLabel(local.cardId);

  // Lower confidence → "check this one" pencil treatment.
  const lowConf = (v: number) => v < 0.75;

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
          committed
            ? "border-moss-soft/60"
            : "border-moss-soft ring-1 ring-moss/20"
        )}
      >
        {/* Merchant + amount headline */}
        <div className="flex items-start justify-between gap-3">
          <EditableField
            label="merchant"
            value={local.merchant}
            confident={!lowConf(local.confidence.merchant)}
            disabled={committed}
            onChange={(v) => setLocal({ ...local, merchant: v })}
            display={
              <span className="font-serif text-lg text-ink lowercase-title">
                {local.merchant}
              </span>
            }
          />
          <EditableField
            label="amount"
            value={(local.amountCents / 100).toString()}
            confident={!lowConf(local.confidence.amount)}
            disabled={committed}
            inputMode="decimal"
            onChange={(v) => {
              const n = parseFloat(v);
              if (!isNaN(n)) {
                setLocal({ ...local, amountCents: Math.round(n * 100) });
              }
            }}
            display={
              <span className="font-serif text-lg tabular text-ink">
                {formatMoney(local.amountCents)}
              </span>
            }
          />
        </div>

        {/* Meta rows */}
        <div className="mt-3 flex flex-col gap-2 border-t border-hairline pt-3">
          <MetaRow
            icon={<Calendar className="h-3.5 w-3.5" />}
            label="date"
            confident={!lowConf(local.confidence.date)}
            disabled={committed}
            value={local.date}
            displayValue={formatShortDate(local.date)}
            inputType="date"
            onChange={(v) => setLocal({ ...local, date: v })}
          />
          <MetaRow
            icon={<CreditCard className="h-3.5 w-3.5" />}
            label="card"
            confident={!lowConf(local.confidence.card)}
            disabled={committed}
            value={local.cardId}
            displayValue={
              local.cardId && card.last4 !== "—"
                ? `${card.name} · ${card.last4}`
                : "Other"
            }
            asSelect="card"
            onChange={(v) => setLocal({ ...local, cardId: v })}
          />
          <MetaRow
            icon={<Tag className="h-3.5 w-3.5" />}
            label="category"
            confident={!lowConf(local.confidence.category)}
            disabled={committed}
            value={local.category}
            displayValue={local.category}
            asSelect="category"
            onChange={(v) => setLocal({ ...local, category: v as Category })}
          />
        </div>

        {/* Action area */}
        {committed ? (
          <div className="mt-4 flex items-center gap-1.5 text-[0.85rem] text-moss-deep">
            <Check className="h-3.5 w-3.5" />
            <span>logged.</span>
          </div>
        ) : (
          <>
            <div className="mt-4 flex flex-col gap-2">
              <button
                type="button"
                onClick={() => onConfirm(local)}
                className="h-11 w-full rounded-2xl bg-moss text-[0.95rem] font-medium text-surface transition-colors hover:bg-moss-deep"
              >
                looks right
              </button>
              <button
                type="button"
                onClick={onFix}
                className="h-10 w-full rounded-2xl border border-hairline text-[0.9rem] text-ink transition-colors hover:bg-sunken/60"
              >
                let me fix it
              </button>
            </div>
            <p className="mt-3 text-center text-[0.72rem] text-ink-tertiary">
              or just tell me what to change.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/* ─── Field primitives ──────────────────────────────────────────── */

interface EditableFieldProps {
  label: string;
  value: string;
  confident: boolean;
  disabled: boolean;
  onChange: (next: string) => void;
  display: React.ReactNode;
  inputMode?: "decimal" | "text";
}

function EditableField({
  label,
  value,
  confident,
  disabled,
  onChange,
  display,
  inputMode = "text",
}: EditableFieldProps) {
  const [editing, setEditing] = useState(false);
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[0.65rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      {editing && !disabled ? (
        <input
          autoFocus
          inputMode={inputMode}
          defaultValue={value}
          onBlur={(e) => {
            onChange(e.currentTarget.value);
            setEditing(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              onChange(e.currentTarget.value);
              setEditing(false);
            }
          }}
          className="w-full bg-transparent font-serif text-lg text-ink focus:outline-none"
        />
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => setEditing(true)}
          className="group flex items-center gap-1.5 text-left disabled:cursor-default"
        >
          {display}
          {!disabled && <PencilGlyph confident={confident} />}
        </button>
      )}
    </div>
  );
}

interface MetaRowProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  displayValue: string;
  confident: boolean;
  disabled: boolean;
  onChange: (next: string) => void;
  inputType?: "date";
  asSelect?: "card" | "category";
}

function MetaRow({
  icon,
  label,
  value,
  displayValue,
  confident,
  disabled,
  onChange,
  inputType,
  asSelect,
}: MetaRowProps) {
  const [editing, setEditing] = useState(false);

  const renderEditor = () => {
    if (asSelect === "card") {
      return (
        <select
          autoFocus
          defaultValue={value}
          onBlur={(e) => {
            onChange(e.currentTarget.value);
            setEditing(false);
          }}
          onChange={(e) => {
            onChange(e.currentTarget.value);
            setEditing(false);
          }}
          className="rounded-md bg-surface px-2 py-1 text-[0.85rem] text-ink focus:outline-none"
        >
          <option value="">Other / Cash</option>
          {FIXTURE_CARDS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} · {c.last4}
            </option>
          ))}
        </select>
      );
    }
    if (asSelect === "category") {
      return (
        <select
          autoFocus
          defaultValue={value}
          onBlur={(e) => {
            onChange(e.currentTarget.value);
            setEditing(false);
          }}
          onChange={(e) => {
            onChange(e.currentTarget.value);
            setEditing(false);
          }}
          className="rounded-md bg-surface px-2 py-1 text-[0.85rem] text-ink focus:outline-none"
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      );
    }
    if (inputType === "date") {
      return (
        <input
          type="date"
          autoFocus
          defaultValue={value}
          onBlur={(e) => {
            onChange(e.currentTarget.value);
            setEditing(false);
          }}
          className="bg-transparent text-[0.85rem] tabular text-ink focus:outline-none"
        />
      );
    }
    return null;
  };

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        {icon}
        <span>{label}</span>
      </div>
      <div className="flex items-center gap-1.5">
        {editing && !disabled ? (
          renderEditor()
        ) : (
          <button
            type="button"
            disabled={disabled}
            onClick={() => setEditing(true)}
            className="flex items-center gap-1.5 text-[0.9rem] text-ink disabled:cursor-default"
          >
            <span className="tabular">{displayValue}</span>
            {!disabled && <PencilGlyph confident={confident} />}
          </button>
        )}
      </div>
    </div>
  );
}

/** Pencil glyph — louder when confidence is low ("check this one"). */
function PencilGlyph({ confident }: { confident: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 w-5 items-center justify-center rounded-full transition-colors",
        confident
          ? "text-ink-quaternary group-hover:text-ink-tertiary"
          : "bg-warn-wash text-warn"
      )}
      aria-label={confident ? "edit" : "double-check this field"}
    >
      <Pencil className={cn("h-3 w-3", !confident && "stroke-[2.2]")} />
    </span>
  );
}
