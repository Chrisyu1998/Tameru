import { useTranslation } from "react-i18next";

/**
 * User-facing privacy disclosure prose. Day 27 (DESIGN.md §9.4 + §9.5).
 *
 * Rendered on both `/privacy` (the route the mobile More menu links to)
 * and `Settings → Privacy` (desktop sidebar). Extracted into a shared
 * component so the two surfaces stay in lockstep — same pattern as the
 * Day 26 `AnalyticsOptOutToggle`.
 *
 * Copy notes:
 *   - The Anthropic block is hedged ("requested, not yet active") and
 *     stays that way until Anthropic confirms ZDR. Update the copy in
 *     this file AND `DESIGN.md` §9.4 in the same PR that records the
 *     confirmation. See `docs/zdr_request.md` for the request log.
 *   - The analytics block names the whitelist (5 events) and the
 *     redaction list (5 categories) explicitly — that specificity is
 *     the privacy story, not a one-liner.
 */
export function PrivacyDisclosure() {
  const { t } = useTranslation();
  return (
    <div className="space-y-5 text-[0.86rem] leading-relaxed text-ink-secondary">
      <section>
        <h3 className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          {t("privacy.disclosure.aiProvidersHeading")}
        </h3>
        <p className="mt-2">
          {t("privacy.disclosure.aiProvidersAnthropicParagraph")}
        </p>
        <p className="mt-2">
          {t("privacy.disclosure.aiProvidersGeminiParagraph")}
        </p>
      </section>

      <section>
        <h3 className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          {t("privacy.disclosure.analyticsHeading")}
        </h3>
        <p className="mt-2">
          {t("privacy.disclosure.analyticsParagraph1")}
        </p>
        <p className="mt-2">
          {t("privacy.disclosure.analyticsParagraph2")}
        </p>
      </section>
    </div>
  );
}
