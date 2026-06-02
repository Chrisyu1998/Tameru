import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ServiceBannerProps {
  message: string;
  onDismiss: () => void;
}

/** Dismissible amber strip at the top of the conversation when AI is down. */
export function ServiceBanner({ message, onDismiss }: ServiceBannerProps) {
  const { t } = useTranslation();
  return (
    <div className="mx-auto mt-2 flex w-full max-w-md items-start gap-3 rounded-2xl border border-warn/30 bg-warn-wash px-4 py-3">
      <p className="flex-1 text-[0.85rem] leading-snug text-ink-secondary">
        {message}
      </p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t("chat.serviceBanner.dismiss")}
        className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-ink-tertiary hover:bg-warn/15 hover:text-ink-secondary"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
