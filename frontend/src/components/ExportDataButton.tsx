import { useState } from "react";
import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";

import { track } from "@/lib/analytics";
import { downloadUserDataExport } from "@/lib/exportApi";
import { cn } from "@/lib/utils";

/**
 * "Export my data" — shared trigger for the JSON download produced by
 * GET /export (Day 27, DESIGN.md §9.6). Rendered on both `/privacy`
 * and `Settings → Privacy`.
 *
 * UI states: idle → loading (with disabled button + spinner copy) →
 * idle. Errors are surfaced as a small inline message under the button
 * rather than a toast — the user is on a privacy-focused page, and a
 * transient banner that vanishes is the wrong affordance for "your
 * data didn't actually download." Refresh-resistant by design.
 *
 * Fires `feature_used` with feature='data_export' on a successful save.
 * Failure paths fire `error_shown` with `internal_error` so the
 * dashboard can count export-attempt drop-offs without exposing what
 * went wrong (network blip, 5xx, etc.).
 */
export function ExportDataButton() {
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorText, setErrorText] = useState<string | null>(null);
  const { t } = useTranslation();

  const handleClick = async () => {
    if (status === "loading") return;
    setStatus("loading");
    setErrorText(null);
    try {
      await downloadUserDataExport();
      track("feature_used", { feature: "data_export" });
      setStatus("idle");
    } catch (err) {
      track("error_shown", { code: "internal_error" });
      setStatus("error");
      setErrorText(
        err instanceof Error
          ? t("privacy.export.errorRetry")
          : t("privacy.export.errorGeneric"),
      );
    }
  };

  return (
    <div className="flex flex-col gap-1.5 py-3.5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[0.95rem] text-ink lowercase-title">
            {t("privacy.export.label")}
          </p>
          <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">
            {t("privacy.export.desc")}
          </p>
        </div>
        <button
          type="button"
          onClick={handleClick}
          disabled={status === "loading"}
          aria-busy={status === "loading"}
          className={cn(
            "inline-flex h-9 flex-shrink-0 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-3 text-sm text-ink hover:bg-sunken",
            status === "loading" && "opacity-50 cursor-not-allowed",
          )}
          data-testid="export-data-button"
        >
          <Download className="h-4 w-4" />
          {status === "loading" ? t("privacy.export.preparing") : t("privacy.export.button")}
        </button>
      </div>
      {status === "error" && errorText && (
        <p
          role="alert"
          className="px-1 text-[0.78rem] text-over"
          data-testid="export-data-error"
        >
          {errorText}
        </p>
      )}
    </div>
  );
}
