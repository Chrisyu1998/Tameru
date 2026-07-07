import { Check, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { formatCurrencyAmount } from "@/lib/format";

/**
 * Ledger-bridge credit suggestion — Phase 2 (DESIGN.md §6.7). Rendered below a
 * committed transaction parse card when the just-logged spend matched an active
 * statement credit on the same card. A tap counts the spend toward the credit
 * (`POST /card-credits/{id}/apply`, atomically clamped server-side).
 *
 * Distinct from the entry-moment `EntryInsightBubble` (which is a passive,
 * no-action aside): this one carries a single action. Once applied it collapses
 * to a confirmed line with no button, so a double-tap can't double-count.
 */
export function CreditSuggestionCard({
  creditName,
  suggestedAmount,
  applying,
  applied,
  error,
  onApply,
}: {
  creditName: string;
  suggestedAmount: string;
  applying?: boolean;
  applied?: boolean;
  error?: string | null;
  onApply: () => void;
}) {
  const { t } = useTranslation();
  const money = (v: string) => formatCurrencyAmount(parseFloat(v) || 0);

  if (applied) {
    return (
      <div className="flex w-full justify-start animate-slide-up-in">
        <div className="flex max-w-[88%] items-center gap-2 rounded-2xl border border-moss bg-moss-wash px-4 py-2.5 text-[0.9rem] text-ink">
          <Check aria-hidden className="h-4 w-4 shrink-0 text-moss" />
          <span>
            {t("chat.creditSuggestion.counted", {
              amount: money(suggestedAmount),
              name: creditName,
            })}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex w-full justify-start animate-slide-up-in">
      <div className="max-w-[88%] rounded-2xl border border-hairline bg-elevated px-4 py-3">
        <div className="flex items-start gap-2">
          <Sparkles aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-moss" />
          <span className="text-[0.9rem] leading-relaxed text-ink">
            {t("chat.creditSuggestion.prompt", {
              amount: money(suggestedAmount),
              name: creditName,
            })}
          </span>
        </div>
        <div className="mt-2.5 flex items-center gap-3">
          <Button variant="secondary" onClick={onApply} disabled={applying}>
            {applying
              ? t("chat.creditSuggestion.applying")
              : t("chat.creditSuggestion.apply")}
          </Button>
          {error && <span className="text-[0.75rem] text-over">{error}</span>}
        </div>
      </div>
    </div>
  );
}
