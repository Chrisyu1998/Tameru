import { SketchIcon } from "@/components/SketchIcon";

/**
 * Replaces the input row entirely when the AI quota is hit.
 * Honest copy: dashboard + edit still work. No retry button.
 */
export function DailyCapCard() {
  return (
    <div className="border-t border-hairline bg-warn-wash px-5 py-5">
      <div className="mx-auto flex max-w-md items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-warn/20 text-warn">
          <SketchIcon kind="sparkle" size={16} seed={71} />
        </div>
        <div className="flex-1">
          <p className="font-serif text-[1rem] text-ink lowercase-title">
            daily ai quota used
          </p>
          <p className="mt-1 text-[0.85rem] leading-relaxed text-ink-secondary">
            i'll be back at midnight UTC. you can still browse your dashboard
            and edit existing transactions in the meantime.
          </p>
        </div>
      </div>
    </div>
  );
}
