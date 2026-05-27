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
  return (
    <div className="space-y-5 text-[0.86rem] leading-relaxed text-ink-secondary">
      <section>
        <h3 className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          ai providers
        </h3>
        <p className="mt-2">
          when you chat with tameru — to add a transaction, to ask a
          question, or anything else — your message goes to anthropic.
          card multiplier lookups also go to anthropic and include only the
          public card name and last 4 digits, never your transaction data.
          anthropic's default retention is 30 days for trust &amp; safety
          review; zero data retention has been requested for the tameru
          org and brings that to zero. anthropic does not use api data for
          training under any tier.
        </p>
        <p className="mt-2">
          we also use google gemini on its paid tier to pick a category
          for each transaction — the merchant name and amount go to
          gemini, nothing else. gemini also parses csv imports and
          receipt photos when you use those. google's paid tier does not
          use api data for training; the free tier does, and we never use
          it.
        </p>
      </section>

      <section>
        <h3 className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          product analytics
        </h3>
        <p className="mt-2">
          we use posthog (us cloud) to count structural events only:
          chat-session starts and ends, feature usage, onboarding steps, the
          weekly digest opens, and shown errors. we never send transaction
          amounts, merchant names, card details, or the text of what you
          ask in chat.
        </p>
        <p className="mt-2">
          the toggle above stops new events immediately. anything we
          already counted stays in posthog until you ask us to remove it.
        </p>
      </section>
    </div>
  );
}
