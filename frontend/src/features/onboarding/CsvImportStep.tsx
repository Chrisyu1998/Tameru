import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { StepDots } from "./StepDots";
import { cn } from "@/lib/utils";

const BANK_HINTS = ["Chase", "Amex", "Citi", "BofA"];

interface CsvImportStepProps {
  onContinue: (filename: string) => void;
  onSkip: () => void;
}

export function CsvImportStep({ onContinue, onSkip }: CsvImportStepProps) {
  const { t } = useTranslation();
  const [filename, setFilename] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setFilename(files[0].name);
  };

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-16 animate-fade-up">
      <StepDots current={2} total={2} label={t("onboarding.csvImport.stepLabel")} />

      <h1 className="mt-6 font-serif text-3xl text-ink lowercase-title">
        {t("onboarding.csvImport.title")}
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        {t("onboarding.csvImport.subtitle")}
      </p>

      <label
        htmlFor="csv-upload"
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "mt-8 flex flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed px-6 py-12 text-center transition-colors cursor-pointer",
          dragOver
            ? "border-moss bg-moss-wash/40"
            : "border-hairline bg-surface hover:bg-elevated"
        )}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-moss-wash text-moss-deep">
          <Upload className="h-5 w-5" />
        </div>
        {filename ? (
          <>
            <span className="font-serif text-base text-ink lowercase-title">
              {filename}
            </span>
            <span className="text-xs text-ink-tertiary">{t("onboarding.csvImport.tapToChooseAnother")}</span>
          </>
        ) : (
          <>
            <span className="font-serif text-base text-ink lowercase-title">
              {t("onboarding.csvImport.dropHere")}
            </span>
            <span className="text-xs text-ink-tertiary">{t("onboarding.csvImport.orTapBrowse")}</span>
          </>
        )}
        <input
          id="csv-upload"
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </label>

      <div className="mt-5">
        <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          {t("onboarding.csvImport.worksWith")}
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {BANK_HINTS.map((b) => (
            <span
              key={b}
              className="rounded-full border border-hairline bg-surface px-3 py-1 text-xs text-ink-secondary"
            >
              {b}
            </span>
          ))}
        </div>
      </div>

      <div className="flex-1" />

      <div className="mt-10 flex flex-col items-center gap-3">
        <Button
          fullWidth
          size="lg"
          disabled={!filename}
          onClick={() => filename && onContinue(filename)}
        >
          {t("onboarding.csvImport.continueWithImport")}
        </Button>
        <button
          type="button"
          onClick={onSkip}
          className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
        >
          {t("onboarding.csvImport.skipForNow")}
        </button>
      </div>
    </div>
  );
}
