import { Button } from "@/components/Button";

interface PhilosophyStepProps {
  onContinue: () => void;
  onTour: () => void;
}

/**
 * The pitch screen. Copy is the verbatim DESIGN.md §5.4.1 block — keep
 * the two in sync. Primary CTA is "get started" so a user cannot reach
 * signin without passing this screen. Secondary text link routes to the
 * static tour.
 */
export function PhilosophyStep({ onContinue, onTour }: PhilosophyStepProps) {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-20 animate-fade-up">
      <h1 className="font-serif text-3xl text-ink lowercase-title">
        why manual?
      </h1>

      <div className="mt-8 flex flex-col gap-5 text-[0.95rem] leading-relaxed text-ink-secondary">
        <p>
          Most spending apps sync your bank automatically. Tameru
          doesn&rsquo;t &mdash; on purpose.
        </p>
        <p>
          The act of logging a purchase, even for 10 seconds, is what
          builds awareness. Mint synced everything automatically. People
          still overspent, because automatic means invisible.
        </p>
        <p>
          Tameru asks you to log what you spend. The AI handles the rest
          &mdash; categorization, patterns, questions, nudges. You bring
          the data. Tameru brings the intelligence.
        </p>
        <p className="text-ink">
          If that sounds like the right trade, let&rsquo;s get started.
        </p>
      </div>

      <div className="flex-1" />

      <div className="mt-10 flex flex-col items-center gap-4">
        <Button fullWidth onClick={onContinue}>
          get started
        </Button>
        <button
          type="button"
          onClick={onTour}
          className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
        >
          take the tour
        </button>
      </div>
    </div>
  );
}
