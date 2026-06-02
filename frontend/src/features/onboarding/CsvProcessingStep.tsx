import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { useTranslation } from "react-i18next";

interface CsvProcessingStepProps {
  filename: string;
  totalRows?: number;
  onComplete: () => void;
}

/** i18n keys for the 4-beat status ticker. Resolved inside the component. */
const STATUS_KEYS = [
  "onboarding.csvProcessing.status1",
  "onboarding.csvProcessing.status2",
  "onboarding.csvProcessing.status3",
  "onboarding.csvProcessing.status4",
] as const;

export function CsvProcessingStep({
  filename,
  totalRows = 143,
  onComplete,
}: CsvProcessingStepProps) {
  const { t } = useTranslation();
  const [count, setCount] = useState(0);
  const [statusIdx, setStatusIdx] = useState(0);

  useEffect(() => {
    const start = performance.now();
    const duration = 3200;
    let raf = 0;

    const tick = (now: number) => {
      const elapsed = Math.min(1, (now - start) / duration);
      setCount(Math.floor(elapsed * totalRows));
      setStatusIdx(
        Math.min(STATUS_KEYS.length - 1, Math.floor(elapsed * STATUS_KEYS.length))
      );
      if (elapsed < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        setTimeout(onComplete, 400);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [totalRows, onComplete]);

  const progress = Math.min(1, count / totalRows);
  const radius = 64;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - progress);

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col items-center justify-center px-6 animate-fade-up">
      <div className="relative flex h-44 w-44 items-center justify-center">
        <svg
          viewBox="0 0 160 160"
          className="absolute inset-0 -rotate-90"
          aria-hidden="true"
        >
          <circle
            cx="80"
            cy="80"
            r={radius}
            fill="none"
            stroke="var(--color-hairline)"
            strokeWidth="3"
          />
          <circle
            cx="80"
            cy="80"
            r={radius}
            fill="none"
            stroke="var(--color-moss)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 80ms linear" }}
          />
        </svg>
        <div className="flex flex-col items-center gap-1">
          <span className="font-serif text-4xl text-ink tabular">{count}</span>
          <span className="text-xs text-ink-tertiary tabular">
            {t("onboarding.csvProcessing.ofTotal", { total: totalRows })}
          </span>
        </div>
      </div>

      <p className="mt-10 font-serif italic text-base text-ink-secondary lowercase-title">
        {t(STATUS_KEYS[statusIdx])}
      </p>

      <div className="mt-6 inline-flex items-center gap-2 rounded-full border border-hairline bg-surface px-3 py-1.5 text-xs text-ink-tertiary">
        <FileText className="h-3 w-3" />
        <span className="max-w-[14rem] truncate">{filename}</span>
      </div>
    </div>
  );
}
