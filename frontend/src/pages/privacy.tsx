import { AnalyticsOptOutToggle } from "@/components/AnalyticsOptOutToggle";
import { DeleteAccountRow } from "@/components/DeleteAccountRow";
import { ExportDataButton } from "@/components/ExportDataButton";
import { PrivacyDisclosure } from "@/components/PrivacyDisclosure";

/**
 * Privacy page reached from the mobile More menu. Renders the shared
 * Day 27 component stack — the desktop `Settings → Privacy` panel
 * renders the same components in the same order so both surfaces stay
 * in lockstep without copy-paste drift.
 */
export default function PrivacyPage() {
  return (
    <div className="mx-auto w-full max-w-md px-5 pt-10 pb-12">
      <h1 className="font-serif text-3xl text-ink lowercase-title">privacy</h1>
      <p className="mt-3 text-sm text-ink-secondary">
        what tameru does with your data, and what it doesn't.
      </p>

      <div className="mt-6 divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <AnalyticsOptOutToggle />
        <ExportDataButton />
        <DeleteAccountRow />
      </div>

      <div className="mt-8">
        <PrivacyDisclosure />
      </div>
    </div>
  );
}
