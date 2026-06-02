import { Mail } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * "Delete my account" affordance. Phase 2 ships an in-app button
 * (DESIGN.md §17.11); v1 routes the request to email so the author can
 * verify each deletion personally at ~10-user scale.
 *
 * The mailto target is the support inbox aliased from the digest
 * `Reply-To` address (DESIGN.md §6.4). Subject and body are pre-filled
 * so the user only has to confirm and hit send.
 *
 * Same dual-surface pattern as the other Day 27 components: rendered
 * on both `/privacy` and `Settings → Privacy`.
 */
export function DeleteAccountRow() {
  const { t } = useTranslation();
  const subject = encodeURIComponent(t("privacy.deleteAccount.emailSubject"));
  const body = encodeURIComponent(
    [
      t("privacy.deleteAccount.emailBodyLine1"),
      "",
      t("privacy.deleteAccount.emailBodyLine2"),
      "",
      t("privacy.deleteAccount.emailBodyLine3"),
      "",
      t("privacy.deleteAccount.emailBodyLine4"),
    ].join("\n"),
  );
  const mailto = `mailto:hello@mail.tameru.xyz?subject=${subject}&body=${body}`;

  return (
    <div className="flex items-start justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <p className="text-[0.95rem] text-ink lowercase-title">
          {t("privacy.deleteAccount.label")}
        </p>
        <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">
          {t("privacy.deleteAccount.desc")}
        </p>
      </div>
      <a
        href={mailto}
        className="inline-flex h-9 flex-shrink-0 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-3 text-sm text-ink hover:bg-sunken"
        data-testid="delete-account-mailto"
      >
        <Mail className="h-4 w-4" />
        {t("privacy.deleteAccount.button")}
      </a>
    </div>
  );
}
