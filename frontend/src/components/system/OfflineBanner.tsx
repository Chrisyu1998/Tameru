import { CloudOff } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Persistent strip shown between top bar and main content while offline.
 * Sunken background tone — calm, not alarming.
 */
export function OfflineBanner() {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 border-b border-hairline bg-sunken px-4 py-2 text-[0.78rem] text-ink-secondary"
    >
      <CloudOff className="h-3.5 w-3.5 flex-shrink-0 text-ink-tertiary" />
      <span>
        {t("chat.offline_banner")}
      </span>
    </div>
  );
}
