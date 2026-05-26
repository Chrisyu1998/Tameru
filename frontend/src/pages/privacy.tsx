import { AnalyticsOptOutToggle } from "@/components/AnalyticsOptOutToggle";

export default function PrivacyPage() {
  return (
    <div className="mx-auto w-full max-w-md px-5 pt-10 pb-12">
      <h1 className="font-serif text-3xl text-ink lowercase-title">privacy</h1>
      <p className="mt-3 text-sm text-ink-secondary">
        tameru keeps your ledger on your device. nothing is sent to our servers
        without your explicit action — full policy lands here.
      </p>

      <div className="mt-6 divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <AnalyticsOptOutToggle />
      </div>
      <p className="mt-3 px-1 text-[0.78rem] text-ink-tertiary">
        events already collected stay until manual deletion.
      </p>
    </div>
  );
}
