import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Plus, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { BottomSheet } from "@/components/BottomSheet";
import { useLedger } from "@/lib/ledger";
import { currencySymbol, formatFullDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  CREDIT_CADENCES,
  confirmCredits,
  deleteCredit,
  getCreditHistory,
  listCredits,
  lookupCredits,
  manualCreditProposal,
  patchCredit,
  type CardCreditHistoryRow,
  type CardCreditRow,
  type CreditCadence,
  type CreditProposal,
} from "@/lib/cardCreditsApi";

/**
 * Credits page (DESIGN.md §6.7) — reached from the "credits" chip on a card
 * row. A per-card list of statement-credit progress bars with set-used-amount,
 * edit, archive, plus first-time setup (lookup → propose-confirm, or manual
 * add). This is a management surface like /subscriptions, not a ledger-create
 * surface — every mutation flows through an explicit HTTP call.
 */
export default function CardCreditsPage() {
  const { cardId = "" } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { cards } = useLedger();
  const card = useMemo(() => cards.find((c) => c.id === cardId), [cards, cardId]);

  const [credits, setCredits] = useState<CardCreditRow[]>([]);
  // Latest closed-period snapshot per credit (Phase 2, §8.18) → the
  // "last period you used $X" line. Best-effort; absent for new credits.
  const [history, setHistory] = useState<Record<string, CardCreditHistoryRow>>(
    {},
  );
  const [loading, setLoading] = useState(true);
  const [proposals, setProposals] = useState<CreditProposal[] | null>(null);
  const [looking, setLooking] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<CardCreditRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!cardId) return;
    try {
      const resp = await listCredits(cardId);
      setCredits(resp.items);
      // Latest closed period per credit (one light call each, bounded by the
      // handful of credits per card). Best-effort — a failure just omits the
      // "last period" line for that credit.
      const entries = await Promise.all(
        resp.items.map(async (c) => {
          try {
            const h = await getCreditHistory(c.id, { limit: 1 });
            return [c.id, h.items[0]] as const;
          } catch {
            return [c.id, undefined] as const;
          }
        }),
      );
      setHistory(
        Object.fromEntries(
          entries.filter((e): e is [string, CardCreditHistoryRow] => !!e[1]),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [cardId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const runLookup = async () => {
    setLooking(true);
    setError(null);
    try {
      const resp = await lookupCredits(cardId);
      // Open the checklist even on a miss — an empty list falls back to the
      // manual-add path in the same sheet.
      setProposals(resp.credits);
    } catch (err) {
      setError(err instanceof Error ? err.message : "lookup failed");
    } finally {
      setLooking(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-8 pb-24">
      <button
        type="button"
        onClick={() => navigate("/cards")}
        className="inline-flex items-center gap-1.5 text-[0.82rem] text-ink-tertiary hover:text-ink"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t("credits.back")}
      </button>

      <header className="mt-3">
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          {t("credits.title")}
        </h1>
        {card && (
          <p className="mt-2 text-sm text-ink-tertiary">
            {card.name} · ···· {card.last4}
          </p>
        )}
      </header>

      {loading ? (
        <p className="mt-8 text-sm text-ink-tertiary">{t("credits.loading")}</p>
      ) : credits.length === 0 ? (
        <EmptyCredits />
      ) : (
        <ul className="mt-6 flex flex-col gap-3">
          {credits.map((credit) => (
            <li key={credit.id}>
              <button
                type="button"
                onClick={() => setEditing(credit)}
                className="block w-full text-left rounded-2xl border border-hairline px-4 py-3.5 hover:bg-elevated"
              >
                <CreditProgress credit={credit} lastPeriod={history[credit.id]} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="mt-4 text-[0.8rem] text-over">{error}</p>}

      <div className="mt-8 flex flex-col gap-3 border-t border-hairline pt-6">
        <Button
          variant="secondary"
          fullWidth
          disabled={looking}
          onClick={() => void runLookup()}
        >
          <Search className="h-4 w-4" />
          {looking ? t("credits.lookingUp") : t("credits.lookUp")}
        </Button>
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="inline-flex items-center justify-center gap-1.5 text-[0.85rem] text-ink-secondary hover:text-ink"
        >
          <Plus className="h-3.5 w-3.5" />
          {t("credits.addManually")}
        </button>
      </div>

      <LookupResultsSheet
        open={proposals !== null}
        cardId={cardId}
        proposals={proposals ?? []}
        onClose={() => setProposals(null)}
        onConfirmed={() => {
          setProposals(null);
          void refresh();
        }}
        onAddManually={() => {
          setProposals(null);
          setAdding(true);
        }}
      />

      <ManualCreditSheet
        open={adding}
        cardId={cardId}
        onClose={() => setAdding(false)}
        onConfirmed={() => {
          setAdding(false);
          void refresh();
        }}
      />

      <EditCreditSheet
        open={editing !== null}
        credit={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          void refresh();
        }}
      />
    </div>
  );
}

function CreditProgress({
  credit,
  lastPeriod,
}: {
  credit: CardCreditRow;
  lastPeriod?: CardCreditHistoryRow;
}) {
  const { t } = useTranslation();
  const used = parseFloat(credit.used_amount) || 0;
  const total = credit.amount != null ? parseFloat(credit.amount) : null;
  const pct =
    total && total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const remainingDays = daysUntil(credit.next_reset_date);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[0.95rem] text-ink">{credit.name}</span>
        <span className="tabular text-[0.8rem] text-ink-tertiary">
          {total != null
            ? `${fmtAmt(used)} / ${fmtAmt(total)}`
            : fmtAmt(used)}
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-sunken">
        <div
          className="h-full rounded-full bg-moss"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1.5 text-[0.72rem] text-ink-quaternary">
        {t("credits.resetsIn", {
          count: Math.max(0, remainingDays),
          date: formatFullDate(credit.next_reset_date),
        })}
      </p>
      {lastPeriod && lastPeriod.amount != null && (
        <p className="mt-0.5 text-[0.72rem] text-ink-quaternary">
          {t("credits.lastPeriod", {
            used: fmtAmt(parseFloat(lastPeriod.used_amount) || 0),
            total: fmtAmt(parseFloat(lastPeriod.amount) || 0),
          })}
        </p>
      )}
    </div>
  );
}

function EmptyCredits() {
  const { t } = useTranslation();
  return (
    <div className="mt-10 rounded-2xl border border-dashed border-hairline px-5 py-8 text-center">
      <p className="font-serif text-lg text-ink lowercase-title">
        {t("credits.empty.heading")}
      </p>
      <p className="mt-1 text-[0.85rem] text-ink-tertiary">
        {t("credits.empty.body")}
      </p>
    </div>
  );
}

function LookupResultsSheet({
  open,
  cardId,
  proposals,
  onClose,
  onConfirmed,
  onAddManually,
}: {
  open: boolean;
  cardId: string;
  proposals: CreditProposal[];
  onClose: () => void;
  onConfirmed: () => void;
  onAddManually: () => void;
}) {
  const { t } = useTranslation();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setChecked(new Set(proposals.map((p) => p.client_request_id)));
    setAmounts(
      Object.fromEntries(
        proposals.map((p) => [p.client_request_id, p.amount ?? ""]),
      ),
    );
    setError(null);
  }, [open, proposals]);

  const confirm = async () => {
    const selected = proposals
      .filter((p) => checked.has(p.client_request_id))
      .map((p) => ({
        ...p,
        amount: (amounts[p.client_request_id] ?? "").trim() || null,
      }));
    if (selected.length === 0) return;
    setSaving(true);
    setError(null);
    try {
      await confirmCredits(selected);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      ariaLabel={t("credits.lookupSheet.ariaLabel")}
      desktopVariant="side"
    >
      <h2 className="font-serif text-xl text-ink lowercase-title">
        {t("credits.lookupSheet.title")}
      </h2>
      {proposals.length === 0 ? (
        <>
          <p className="mt-2 text-[0.85rem] text-ink-tertiary">
            {t("credits.lookupSheet.none")}
          </p>
          <div className="mt-6">
            <Button fullWidth variant="secondary" onClick={onAddManually}>
              {t("credits.addManually")}
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="mt-1 text-[0.78rem] text-ink-tertiary">
            {t("credits.lookupSheet.subtitle")}
          </p>
          <ul className="mt-4 flex flex-col gap-2">
            {proposals.map((p) => {
              const on = checked.has(p.client_request_id);
              return (
                <li
                  key={p.client_request_id}
                  className="rounded-2xl border border-hairline px-3 py-2.5"
                >
                  <label className="flex items-start gap-2.5">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={(e) => {
                        setChecked((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(p.client_request_id);
                          else next.delete(p.client_request_id);
                          return next;
                        });
                      }}
                      className="mt-1 accent-moss"
                    />
                    <div className="flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[0.9rem] text-ink">{p.name}</span>
                        <span className="text-[0.7rem] uppercase tracking-wider text-ink-quaternary">
                          {t(`credits.cadence.${p.cadence}`)}
                        </span>
                      </div>
                      <div className="mt-1.5 flex items-center gap-1">
                        <span className="font-serif text-ink-tertiary">
                          {currencySymbol()}
                        </span>
                        <input
                          value={amounts[p.client_request_id] ?? ""}
                          onChange={(e) =>
                            setAmounts((prev) => ({
                              ...prev,
                              [p.client_request_id]: e.target.value,
                            }))
                          }
                          inputMode="decimal"
                          placeholder="0"
                          disabled={!on}
                          className="w-24 bg-transparent text-[0.9rem] tabular text-ink focus:outline-none disabled:opacity-40"
                        />
                      </div>
                    </div>
                  </label>
                </li>
              );
            })}
          </ul>
          {error && <p className="mt-3 text-[0.78rem] text-over">{error}</p>}
          <div className="mt-6 flex flex-col gap-3">
            <Button
              fullWidth
              disabled={saving || checked.size === 0}
              onClick={() => void confirm()}
            >
              {saving
                ? t("credits.saving")
                : t("credits.lookupSheet.confirm", { count: checked.size })}
            </Button>
            <button
              type="button"
              onClick={onAddManually}
              className="self-center text-sm text-ink-secondary hover:text-ink"
            >
              {t("credits.addManually")}
            </button>
          </div>
        </>
      )}
    </BottomSheet>
  );
}

function ManualCreditSheet({
  open,
  cardId,
  onClose,
  onConfirmed,
}: {
  open: boolean;
  cardId: string;
  onClose: () => void;
  onConfirmed: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [cadence, setCadence] = useState<CreditCadence>("quarterly");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setAmount("");
    setCadence("quarterly");
    setError(null);
  }, [open]);

  const valid = name.trim().length > 0;

  const save = async () => {
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      await confirmCredits([
        manualCreditProposal(cardId, {
          name: name.trim(),
          amount: amount.trim() || null,
          cadence,
        }),
      ]);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      ariaLabel={t("credits.manualSheet.ariaLabel")}
      desktopVariant="side"
    >
      <h2 className="font-serif text-xl text-ink lowercase-title">
        {t("credits.manualSheet.title")}
      </h2>
      <div className="mt-5 flex flex-col gap-4">
        <Field label={t("credits.fields.name")}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("credits.fields.namePlaceholder")}
            className="w-full bg-transparent text-[0.95rem] text-ink focus:outline-none"
          />
        </Field>
        <Field label={t("credits.fields.amount")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">
              {currencySymbol()}
            </span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="0"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </Field>
        <Field label={t("credits.fields.cadence")}>
          <CadencePicker value={cadence} onChange={setCadence} />
        </Field>
        {error && <p className="text-[0.78rem] text-over">{error}</p>}
      </div>
      <div className="mt-7">
        <Button fullWidth disabled={!valid || saving} onClick={() => void save()}>
          {saving ? t("credits.saving") : t("credits.manualSheet.add")}
        </Button>
      </div>
    </BottomSheet>
  );
}

function EditCreditSheet({
  open,
  credit,
  onClose,
  onSaved,
}: {
  open: boolean;
  credit: CardCreditRow | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [used, setUsed] = useState("");
  const [amount, setAmount] = useState("");
  const [name, setName] = useState("");
  const [cadence, setCadence] = useState<CreditCadence>("quarterly");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !credit) return;
    setUsed(credit.used_amount ?? "");
    setAmount(credit.amount ?? "");
    setName(credit.name);
    setCadence(credit.cadence);
    setError(null);
  }, [open, credit]);

  if (!credit) {
    return (
      <BottomSheet open={open} onClose={onClose} desktopVariant="side">
        {null}
      </BottomSheet>
    );
  }

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    const patch: Parameters<typeof patchCredit>[1] = {};
    if (used.trim() !== (credit.used_amount ?? "")) {
      patch.used_amount = used.trim() === "" ? "0" : used.trim();
    }
    if (amount.trim() !== (credit.amount ?? "")) {
      patch.amount = amount.trim() === "" ? null : amount.trim();
    }
    if (name.trim() !== credit.name) patch.name = name.trim();
    if (cadence !== credit.cadence) patch.cadence = cadence;
    if (Object.keys(patch).length === 0) {
      onClose();
      setSaving(false);
      return;
    }
    try {
      await patchCredit(credit.id, patch);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  const archive = async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await deleteCredit(credit.id);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      ariaLabel={t("credits.editSheet.ariaLabel")}
      desktopVariant="side"
    >
      <h2 className="font-serif text-xl text-ink lowercase-title">
        {t("credits.editSheet.title")}
      </h2>
      <div className="mt-5 flex flex-col gap-4">
        <Field label={t("credits.fields.used")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">
              {currencySymbol()}
            </span>
            <input
              value={used}
              onChange={(e) => setUsed(e.target.value)}
              inputMode="decimal"
              placeholder="0"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </Field>
        <Field label={t("credits.fields.name")}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full bg-transparent text-[0.95rem] text-ink focus:outline-none"
          />
        </Field>
        <Field label={t("credits.fields.amount")}>
          <div className="flex items-center gap-1">
            <span className="font-serif text-ink-tertiary">
              {currencySymbol()}
            </span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="0"
              className="w-full bg-transparent text-[0.95rem] tabular text-ink focus:outline-none"
            />
          </div>
        </Field>
        <Field label={t("credits.fields.cadence")}>
          <CadencePicker value={cadence} onChange={setCadence} />
        </Field>
        {error && <p className="text-[0.78rem] text-over">{error}</p>}
      </div>
      <div className="mt-7 flex flex-col gap-3">
        <Button fullWidth disabled={saving} onClick={() => void save()}>
          {saving ? t("credits.saving") : t("credits.saveChanges")}
        </Button>
        <button
          type="button"
          onClick={() => void archive()}
          disabled={saving}
          className="self-center text-sm text-over hover:underline underline-offset-4 disabled:opacity-50"
        >
          {t("credits.editSheet.stopTracking")}
        </button>
      </div>
    </BottomSheet>
  );
}

function CadencePicker({
  value,
  onChange,
}: {
  value: CreditCadence;
  onChange: (c: CreditCadence) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-1.5">
      {CREDIT_CADENCES.map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => onChange(c)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-[0.72rem]",
            value === c
              ? "border-moss bg-moss text-surface"
              : "border-hairline text-ink-tertiary hover:bg-elevated",
          )}
        >
          {t(`credits.cadence.${c}`)}
        </button>
      ))}
    </div>
  );
}

function Field({
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

// "$550" reads cleaner than "$550.00"; keep decimals only when present.
function fmtAmt(value: number): string {
  const symbol = currencySymbol();
  const n = value % 1 === 0 ? value.toFixed(0) : value.toFixed(2);
  return `${symbol}${n}`;
}

function daysUntil(iso: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(`${iso}T00:00:00`);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}
