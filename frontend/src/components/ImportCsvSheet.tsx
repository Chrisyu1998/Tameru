import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FileText, Upload, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BottomSheet } from "@/components/BottomSheet";
import { Button } from "@/components/Button";
import { ledger, useLedger } from "@/lib/ledger";
import {
  isManualMapping,
  previewCsv,
  type ColumnMapping,
  type ColumnPreview,
  type CsvCommitDone,
  type CsvCommitProgress,
  type ManualMappingPreview,
  type PreviewResponse,
} from "@/lib/importsApi";
import { commitCsv, type ImportStreamError } from "@/lib/imports_stream";
import { ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";
import { cn } from "@/lib/utils";

/**
 * Day 20 — CSV bank import surface (Settings → Import Data).
 *
 * Four phases:
 *   1. `select` — user picks a file and a card.
 *   2. `confirm` / `manual_mapping` — preview returned; user confirms
 *      Gemini's column guess, or maps columns by hand on low confidence.
 *   3. `committing` — SSE stream from /imports/csv/commit; per-row
 *      progress bar + cancel affordance.
 *   4. `done` — counter summary (inserted, duplicates, refunds, foreign).
 *
 * `error` is a sibling phase entered from any of the above; re-entry
 * resets the flow. Re-uploading after an error is the documented
 * recovery path — DESIGN.md §5.4.3 "Idempotent re-run".
 *
 * The route's `import_token` survives across the preview→commit hop;
 * we just hold it in state along with the original File so /commit can
 * re-upload the same bytes (no server-side blob storage).
 */
type Phase =
  | { kind: "select" }
  | { kind: "previewing" }
  | { kind: "confirm"; preview: ColumnPreview; file: File }
  | { kind: "manual_mapping"; preview: ManualMappingPreview; file: File }
  | {
      kind: "committing";
      file: File;
      preview: ColumnPreview | ManualMappingPreview;
      mapping: ColumnMapping;
      progress: CsvCommitProgress | null;
    }
  | { kind: "done"; result: CsvCommitDone }
  | { kind: "error"; message: string; code?: string };

interface ImportCsvSheetProps {
  open: boolean;
  onClose: () => void;
}

export function ImportCsvSheet({ open, onClose }: ImportCsvSheetProps) {
  /**
   * Render the multi-step CSV import sheet.
   *
   * Closes are blocked during `committing` so a stray scrim tap can't
   * orphan a half-imported file (the dedup quadruple makes recovery
   * safe, but it's still better UX to let the stream finish or use the
   * explicit cancel).
   */
  const { t } = useTranslation();
  const { cards } = useLedger();
  const [phase, setPhase] = useState<Phase>({ kind: "select" });
  const [pickedFile, setPickedFile] = useState<File | null>(null);
  const [pickedCardId, setPickedCardId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Default the card picker to the first available card on open. Use
  // `cards[0]?.id` (string, stable across re-fetches that return the
  // same head card) instead of the cards array itself so a parent
  // re-render that returns a new array reference doesn't re-fire this
  // effect and stomp a user-picked selection.
  const firstCardId = cards[0]?.id ?? null;
  useEffect(() => {
    if (open) {
      setPhase({ kind: "select" });
      setPickedFile(null);
      setPickedCardId(firstCardId);
    }
  }, [open, firstCardId]);

  useEffect(() => {
    return () => {
      // Sheet unmounted mid-commit — abort the stream so the fetch
      // doesn't outlive the UI.
      abortRef.current?.abort();
    };
  }, []);

  async function handlePreview() {
    /** Step 1 → 2: upload the file and read back the column mapping. */
    if (!pickedFile || !pickedCardId) return;
    setPhase({ kind: "previewing" });
    try {
      const resp: PreviewResponse = await previewCsv(pickedFile, pickedCardId);
      if (isManualMapping(resp)) {
        setPhase({ kind: "manual_mapping", preview: resp, file: pickedFile });
      } else {
        setPhase({ kind: "confirm", preview: resp, file: pickedFile });
      }
    } catch (err) {
      setPhase({
        kind: "error",
        code: errorCode(err),
        message: errorMessage(err),
      });
    }
  }

  async function handleCommit(
    file: File,
    preview: ColumnPreview | ManualMappingPreview,
    mapping: ColumnMapping,
  ) {
    /** Step 2 → 3 → 4: stream the commit, render progress, then done. */
    if (!pickedCardId) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase({
      kind: "committing",
      file,
      preview,
      mapping,
      progress: null,
    });
    await commitCsv({
      file,
      cardId: pickedCardId,
      importToken: preview.import_token,
      columnMapping: mapping,
      signal: controller.signal,
      onProgress: (p) =>
        setPhase((current) =>
          current.kind === "committing" ? { ...current, progress: p } : current,
        ),
      onDone: (result) => {
        setPhase({ kind: "done", result });
        // Fire feature_used only when the import actually committed at
        // least one row. A zero-insert run (all duplicates / all skips)
        // isn't a use of the import feature in the analytics sense.
        if (result.inserted > 0) {
          track("feature_used", { feature: "csv_import" });
          // Refresh the ledger so /breakdown, sidebar totals, etc.
          // reflect the imported rows immediately. Fire-and-forget —
          // a failure logs in `ledger.refresh()` and the user can
          // hard-reload as a manual fallback.
          void ledger.refresh();
        }
      },
      onError: (e: ImportStreamError) => {
        if (e.code === "ABORTED") return;
        setPhase({ kind: "error", code: e.code, message: e.message });
      },
    });
    abortRef.current = null;
  }

  function handleCancelCommit() {
    /** User-initiated abort during streaming. */
    abortRef.current?.abort();
    abortRef.current = null;
    // Stay in "committing" briefly until the abort settles, then drop
    // back to the select step so the user can retry.
    setPhase({ kind: "select" });
  }

  const busy = phase.kind === "previewing" || phase.kind === "committing";

  return (
    <BottomSheet
      open={open}
      onClose={() => {
        if (busy) return;
        onClose();
      }}
      blockDismiss={busy}
      ariaLabel="import csv"
    >
      <header className="mb-5">
        <h2 className="font-serif text-2xl text-ink lowercase-title">
          {t("importCsv.title")}
        </h2>
        <p className="mt-1 text-sm text-ink-tertiary">
          {t("importCsv.subtitle")}
        </p>
      </header>

      {phase.kind === "select" && (
        <SelectStep
          cards={cards.map((c) => ({ id: c.id, name: c.name, last4: c.last4 }))}
          pickedFile={pickedFile}
          pickedCardId={pickedCardId}
          fileInputRef={fileInputRef}
          onPickFile={setPickedFile}
          onPickCard={setPickedCardId}
          onNext={handlePreview}
          onCancel={onClose}
        />
      )}

      {phase.kind === "previewing" && <BusyStep label={t("importCsv.detectingColumns")} />}

      {phase.kind === "confirm" && (
        <ConfirmStep
          preview={phase.preview}
          onConfirm={(mapping) =>
            handleCommit(phase.file, phase.preview, mapping)
          }
          onSwitchToManual={() =>
            setPhase({
              kind: "manual_mapping",
              preview: {
                needs_manual_mapping: true,
                headers: Object.keys(phase.preview.sample_rows[0] ?? {}),
                sample_rows: phase.preview.sample_rows,
                import_token: phase.preview.import_token,
                total_rows: phase.preview.total_rows,
              },
              file: phase.file,
            })
          }
          onBack={() => setPhase({ kind: "select" })}
        />
      )}

      {phase.kind === "manual_mapping" && (
        <ManualMappingStep
          preview={phase.preview}
          onConfirm={(mapping) =>
            handleCommit(phase.file, phase.preview, mapping)
          }
          onBack={() => setPhase({ kind: "select" })}
        />
      )}

      {phase.kind === "committing" && (
        <CommittingStep
          progress={phase.progress}
          onCancel={handleCancelCommit}
        />
      )}

      {phase.kind === "done" && (
        <DoneStep result={phase.result} onClose={onClose} />
      )}

      {phase.kind === "error" && (
        <ErrorStep
          code={phase.code}
          message={phase.message}
          onRetry={() => setPhase({ kind: "select" })}
        />
      )}
    </BottomSheet>
  );
}

// ---------------------------------------------------------------------------
// Step components.
// ---------------------------------------------------------------------------

interface CardPickerOption {
  id: string;
  name: string;
  last4: string;
}

function SelectStep({
  cards,
  pickedFile,
  pickedCardId,
  fileInputRef,
  onPickFile,
  onPickCard,
  onNext,
  onCancel,
}: {
  cards: CardPickerOption[];
  pickedFile: File | null;
  pickedCardId: string | null;
  fileInputRef: React.MutableRefObject<HTMLInputElement | null>;
  onPickFile: (file: File | null) => void;
  onPickCard: (id: string) => void;
  onNext: () => void;
  onCancel: () => void;
}) {
  /** Step 1 — pick a CSV, pick a card, click `next`. */
  const { t } = useTranslation();
  const ready = pickedFile !== null && pickedCardId !== null;
  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
          {t("importCsv.select.fileLabel")}
        </h3>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            onPickFile(f);
          }}
          data-testid="csv-file-input"
        />
        {pickedFile ? (
          <div className="mt-2 flex items-center gap-3 rounded-2xl border border-hairline bg-surface px-4 py-3">
            <FileText className="h-4 w-4 text-ink-tertiary" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[0.95rem] text-ink">
                {pickedFile.name}
              </p>
              <p className="text-[0.72rem] text-ink-tertiary">
                {formatBytes(pickedFile.size)}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onPickFile(null)}
              aria-label={t("importCsv.select.removeFile")}
              className="text-ink-tertiary hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="mt-2 inline-flex h-10 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-4 text-sm text-ink hover:bg-sunken"
          >
            <Upload className="h-4 w-4" />
            {t("importCsv.select.chooseCsv")}
          </button>
        )}
        <p className="mt-2 text-[0.78rem] text-ink-tertiary">
          {t("importCsv.select.fileHint")}
        </p>
      </section>

      <section>
        <h3 className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
          {t("importCsv.select.whichCard")}
        </h3>
        {cards.length === 0 ? (
          <p className="mt-2 rounded-2xl border border-hairline bg-surface px-4 py-3 text-[0.9rem] text-ink-tertiary">
            {t("importCsv.select.noCards")}
          </p>
        ) : (
          <ul className="mt-2 divide-y divide-hairline rounded-2xl border border-hairline bg-surface">
            {cards.map((c) => (
              <li key={c.id}>
                <label className="flex cursor-pointer items-center gap-3 px-4 py-3 hover:bg-elevated">
                  <input
                    type="radio"
                    name="import-card"
                    value={c.id}
                    checked={pickedCardId === c.id}
                    onChange={() => onPickCard(c.id)}
                    className="h-4 w-4"
                  />
                  <span className="flex-1 text-[0.95rem] text-ink">
                    {c.name}
                  </span>
                  <span className="text-[0.78rem] text-ink-tertiary">
                    ····{c.last4}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="flex justify-end gap-2 pt-2">
        <Button variant="tertiary" onClick={onCancel}>
          {t("importCsv.cancel")}
        </Button>
        <Button onClick={onNext} disabled={!ready}>
          {t("importCsv.next")}
        </Button>
      </div>
    </div>
  );
}

function BusyStep({ label }: { label: string }) {
  /** Generic intermediate spinner state. */
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-center">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-hairline border-t-ink" />
      <p className="text-[0.9rem] text-ink-tertiary">{label}</p>
    </div>
  );
}

function ConfirmStep({
  preview,
  onConfirm,
  onSwitchToManual,
  onBack,
}: {
  preview: ColumnPreview;
  onConfirm: (mapping: ColumnMapping) => void;
  onSwitchToManual: () => void;
  onBack: () => void;
}) {
  /** Step 2 — high-confidence branch: show Gemini's guess for approval. */
  const { t } = useTranslation();
  const mapping = preview.detected_columns;
  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
          {t("importCsv.confirm.tameruThinks")}
        </h3>
        <dl className="mt-2 divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
          <MappingRow label={t("importCsv.confirm.date")} value={mapping.date} />
          <MappingRow label={t("importCsv.confirm.merchant")} value={mapping.merchant} />
          <MappingRow label={t("importCsv.confirm.amount")} value={mapping.amount} />
          {mapping.currency && (
            <MappingRow label={t("importCsv.confirm.currency")} value={mapping.currency} />
          )}
        </dl>
        <p className="mt-2 text-[0.78rem] text-ink-tertiary">
          {t("importCsv.confirm.rowsConfidence", {
            rows: preview.total_rows.toLocaleString(),
            pct: Math.round(preview.confidence * 100),
          })}
        </p>
      </section>

      <section>
        <h3 className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
          {t("importCsv.confirm.firstRows")}
        </h3>
        <SampleRowsTable rows={preview.sample_rows} />
      </section>

      <button
        type="button"
        onClick={onSwitchToManual}
        className="text-[0.85rem] text-ink-tertiary underline-offset-2 hover:underline"
      >
        {t("importCsv.confirm.wrongMapThem")}
      </button>

      <div className="flex justify-end gap-2 pt-2">
        <Button variant="tertiary" onClick={onBack}>
          {t("importCsv.back")}
        </Button>
        <Button onClick={() => onConfirm(mapping)}>{t("importCsv.confirm.looksRight")}</Button>
      </div>
    </div>
  );
}

function ManualMappingStep({
  preview,
  onConfirm,
  onBack,
}: {
  preview: ManualMappingPreview;
  onConfirm: (mapping: ColumnMapping) => void;
  onBack: () => void;
}) {
  /** Step 2 — low-confidence (or user-requested) manual column picker. */
  const { t } = useTranslation();
  const [date, setDate] = useState<string>(preview.headers[0] ?? "");
  const [merchant, setMerchant] = useState<string>(preview.headers[1] ?? "");
  const [amount, setAmount] = useState<string>(preview.headers[2] ?? "");
  const [currency, setCurrency] = useState<string>("");
  // Default to charges_positive — the more common monthly-statement
  // convention. User flips this when their export uses negative numbers
  // for purchases (Chase activity, Citi activity export shapes).
  const [chargesAreNegative, setChargesAreNegative] = useState<boolean>(false);

  const ready =
    date !== "" &&
    merchant !== "" &&
    amount !== "" &&
    date !== merchant &&
    date !== amount &&
    merchant !== amount;

  function handleSubmit() {
    /** Build the ColumnMapping from the picker state and submit. */
    onConfirm({
      date,
      merchant,
      amount,
      currency: currency === "" ? null : currency,
      sign_convention: chargesAreNegative
        ? "charges_negative"
        : "charges_positive",
      // Self-report confidence at 1.0 — the user explicitly picked
      // these columns, so the backend doesn't need to fall back to
      // its own threshold for anything.
      confidence: 1.0,
    });
  }

  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
          {t("importCsv.manual.mapColumns")}
        </h3>
        <div className="mt-2 space-y-3">
          <ColumnSelect
            label={t("importCsv.manual.dateColumn")}
            value={date}
            options={preview.headers}
            onChange={setDate}
          />
          <ColumnSelect
            label={t("importCsv.manual.merchantColumn")}
            value={merchant}
            options={preview.headers}
            onChange={setMerchant}
          />
          <ColumnSelect
            label={t("importCsv.manual.amountColumn")}
            value={amount}
            options={preview.headers}
            onChange={setAmount}
          />
          <ColumnSelect
            label={t("importCsv.manual.currencyColumn")}
            value={currency}
            options={["", ...preview.headers]}
            onChange={setCurrency}
            allowEmpty
          />
          <label className="mt-2 flex items-start gap-2 px-1 py-1 text-[0.85rem] text-ink">
            <input
              type="checkbox"
              checked={chargesAreNegative}
              onChange={(e) => setChargesAreNegative(e.target.checked)}
              className="mt-0.5 h-4 w-4"
              data-testid="manual-mapping-negative-charges"
            />
            <span>
              {t("importCsv.manual.chargesNegativeLabel")}
              <span className="block text-[0.72rem] text-ink-tertiary">
                {t("importCsv.manual.chargesNegativeHint")}
              </span>
            </span>
          </label>
        </div>
      </section>

      <section>
        <h3 className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
          {t("importCsv.confirm.firstRows")}
        </h3>
        <SampleRowsTable rows={preview.sample_rows} />
      </section>

      <div className="flex justify-end gap-2 pt-2">
        <Button variant="tertiary" onClick={onBack}>
          {t("importCsv.back")}
        </Button>
        <Button onClick={handleSubmit} disabled={!ready}>
          {t("importCsv.import")}
        </Button>
      </div>
    </div>
  );
}

function CommittingStep({
  progress,
  onCancel,
}: {
  progress: CsvCommitProgress | null;
  onCancel: () => void;
}) {
  /** Step 3 — SSE in flight; render progress bar + cancel. */
  const { t } = useTranslation();
  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
      : 0;
  return (
    <div className="space-y-5">
      <div>
        <p className="text-[0.95rem] text-ink lowercase-title">{t("importCsv.committing.importing")}</p>
        <p className="mt-1 text-[0.78rem] text-ink-tertiary">
          {progress
            ? t("importCsv.committing.progress", {
                processed: progress.processed.toLocaleString(),
                total: progress.total.toLocaleString(),
              })
            : t("importCsv.committing.startingUp")}
        </p>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-sunken">
        <div
          className="h-full bg-ink transition-[width] duration-150"
          style={{ width: `${pct}%` }}
        />
      </div>
      {progress?.current_category && (
        <p className="text-[0.78rem] text-ink-tertiary">
          {t("importCsv.committing.categorizingAs")}{" "}
          <span className="text-ink">{progress.current_category}</span>
        </p>
      )}
      <div className="flex justify-end pt-2">
        <Button variant="tertiary" onClick={onCancel}>
          {t("importCsv.cancel")}
        </Button>
      </div>
    </div>
  );
}

function DoneStep({
  result,
  onClose,
}: {
  result: CsvCommitDone;
  onClose: () => void;
}) {
  /** Step 4 — summary of the four counters. */
  const { t } = useTranslation();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3 rounded-2xl border border-hairline bg-surface px-4 py-4">
        <CheckCircle2 className="h-5 w-5 text-moss" />
        <div>
          <p className="text-[0.95rem] text-ink lowercase-title">{t("importCsv.done.allSet")}</p>
          <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">
            {t("importCsv.done.transactionsImported", { count: result.inserted.toLocaleString() })}
          </p>
        </div>
      </div>
      <ul className="divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <SummaryRow label={t("importCsv.done.imported")} value={result.inserted} />
        <SummaryRow label={t("importCsv.done.duplicatesSkipped")} value={result.skipped_duplicates} />
        <SummaryRow label={t("importCsv.done.refundsSkipped")} value={result.skipped_refunds} />
        <SummaryRow
          label={t("importCsv.done.foreignSkipped")}
          value={result.skipped_foreign_currency}
        />
        <SummaryRow
          label={t("importCsv.done.couldntRead")}
          value={result.skipped_parse_errors}
        />
      </ul>
      <div className="flex justify-end pt-2">
        <Button onClick={onClose}>{t("importCsv.done.done")}</Button>
      </div>
    </div>
  );
}

function ErrorStep({
  code,
  message,
  onRetry,
}: {
  code?: string;
  message: string;
  onRetry: () => void;
}) {
  /** Sibling state — entered from any step on failure. */
  const { t } = useTranslation();
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3 rounded-2xl border border-hairline bg-surface px-4 py-4">
        <AlertTriangle className="mt-0.5 h-5 w-5 text-ink-tertiary" />
        <div className="min-w-0">
          <p className="text-[0.95rem] text-ink lowercase-title">
            {t("importCsv.error.title")}
          </p>
          <p className="mt-0.5 break-words text-[0.78rem] text-ink-tertiary">
            {message}
            {code && (
              <span className="ml-1 rounded bg-sunken px-1 font-mono text-[0.7rem] text-ink-secondary">
                {code}
              </span>
            )}
          </p>
        </div>
      </div>
      <p className="text-[0.78rem] text-ink-tertiary">
        {t("importCsv.error.safeToRetry")}
      </p>
      <div className="flex justify-end pt-2">
        <Button onClick={onRetry}>{t("importCsv.error.tryAgain")}</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small reusable bits.
// ---------------------------------------------------------------------------

function MappingRow({ label, value }: { label: string; value: string }) {
  /** Label/value row used inside the confirm-step list. */
  return (
    <div className="flex items-center justify-between gap-3 py-3 first:pt-3 last:pb-3">
      <dt className="text-[0.78rem] text-ink-tertiary">{label}</dt>
      <dd className="text-[0.95rem] text-ink">{value}</dd>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: number }) {
  /** Counter row in the done-step summary panel. */
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-[0.85rem] text-ink-tertiary">{label}</span>
      <span
        className={cn(
          "tabular-nums text-[0.95rem]",
          value > 0 ? "text-ink" : "text-ink-quaternary",
        )}
      >
        {value.toLocaleString()}
      </span>
    </div>
  );
}

function ColumnSelect({
  label,
  value,
  options,
  onChange,
  allowEmpty = false,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  allowEmpty?: boolean;
}) {
  /** Native select for the manual-mapping picker — no Radix dep needed. */
  const { t } = useTranslation();
  const noneLabel = t("importCsv.manual.none");
  return (
    <label className="block">
      <span className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 h-10 w-full rounded-2xl border border-hairline bg-surface px-3 text-[0.9rem] text-ink hover:bg-elevated focus:outline-none focus:ring-1 focus:ring-ink"
      >
        {allowEmpty && <option value="">{noneLabel}</option>}
        {options
          .filter((o) => o !== "" || allowEmpty)
          .map((o) => (
            <option key={o || "__empty"} value={o}>
              {o || noneLabel}
            </option>
          ))}
      </select>
    </label>
  );
}

function SampleRowsTable({ rows }: { rows: Record<string, string>[] }) {
  /** Wide-table preview of the first N data rows from the upload. */
  const { t } = useTranslation();
  const headers = useMemo(() => Object.keys(rows[0] ?? {}), [rows]);
  if (rows.length === 0 || headers.length === 0) {
    return (
      <p className="mt-2 text-[0.78rem] text-ink-tertiary">{t("importCsv.noRowsToPreview")}</p>
    );
  }
  return (
    <div className="mt-2 overflow-x-auto rounded-2xl border border-hairline bg-surface">
      <table className="w-full text-[0.78rem]">
        <thead>
          <tr className="border-b border-hairline text-ink-tertiary">
            {headers.map((h) => (
              <th key={h} className="px-3 py-2 text-left font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-hairline last:border-b-0">
              {headers.map((h) => (
                <td
                  key={h}
                  className="max-w-[12rem] truncate px-3 py-2 text-ink"
                >
                  {row[h] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatBytes(n: number): string {
  /** Compact byte size string for the picked-file row. */
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function errorCode(err: unknown): string | undefined {
  /** Pull the structured code off an ApiError body, if present. */
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: { code?: string } } | null)?.detail;
    if (detail && typeof detail.code === "string") return detail.code;
    return `HTTP_${err.status}`;
  }
  return undefined;
}

function errorMessage(err: unknown): string {
  /** Render an ApiError or unknown as a user-readable message. */
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: { message?: string } } | null)?.detail;
    if (detail && typeof detail.message === "string") return detail.message;
    return `the server returned ${err.status}.`;
  }
  if (err instanceof Error) return err.message;
  return "something went wrong.";
}
