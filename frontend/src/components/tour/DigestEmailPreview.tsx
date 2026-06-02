import { Mail } from "lucide-react";
import { useTranslation } from "react-i18next";
import { tourDigest } from "@/fixtures/tour";
import { cn } from "@/lib/utils";

/**
 * Email-shaped preview of the weekly digest. Subject + from + preheader
 * sit in an email-style header band; the body holds the kind, quiet
 * bullets the real Resend email will render in Day 25. Day 21 ships the
 * minimal version for the guided tour; Day 25 can either reuse this
 * presentation or replace it with the production email layout.
 */
export function DigestEmailPreview() {
  const { t } = useTranslation();
  return (
    <div className="overflow-hidden rounded-2xl border border-hairline bg-surface shadow-sm">
      <div className="border-b border-hairline bg-sunken/50 px-4 py-3">
        <div className="flex items-center gap-2 text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          <Mail className="h-3 w-3" strokeWidth={2} />
          <span>{t("tour.digest.inbox")}</span>
        </div>
        <p className="mt-2 font-serif text-lg text-ink lowercase-title">
          {tourDigest.subject}
        </p>
        <p className="mt-0.5 text-[0.7rem] text-ink-tertiary">
          {tourDigest.from} · {t("tour.digest.time")}
        </p>
      </div>

      <div className="px-5 pt-5 pb-6">
        <p className="font-serif italic text-ink-secondary lowercase-title">
          {tourDigest.preheader}
        </p>

        <ul className="mt-5 flex flex-col gap-3 text-sm text-ink-secondary">
          {tourDigest.bullets.map((b, i) => (
            <li key={i} className="flex items-start gap-2.5">
              <span
                className={cn(
                  "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                  b.tone === "good" && "bg-moss",
                  b.tone === "warn" && "bg-warn"
                )}
              />
              <span>{b.text}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
