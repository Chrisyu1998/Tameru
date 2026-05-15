import { Button } from "@/components/Button";

interface PhilosophyStepProps {
  onContinue: () => void;
  onTour: () => void;
}

export function PhilosophyStep({ onContinue, onTour }: PhilosophyStepProps) {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-24 animate-fade-up">
      <h1 className="font-serif text-3xl text-ink lowercase-title">why manual?</h1>

      <div className="mt-8 flex flex-col gap-5 text-[0.95rem] leading-relaxed text-ink-secondary">
        <p>
          Most apps pull your transactions in silence. The numbers move, the
          balance shifts, and you never quite see it happen.
        </p>
        <p>
          Automatic means invisible. The act of logging is what builds awareness
          — a half-second pause where you notice what you bought, and why.
        </p>
        <p>
          Manual entry isn't a chore here. It's the entire point. A small ritual
          that turns spending into something you remember instead of something
          that drifts past.
        </p>
        <p className="text-ink-tertiary">
          We'll keep it light. A few taps, then back to your day.
        </p>
      </div>

      <div className="flex-1" />

      <div className="mt-12 flex flex-col items-center gap-4">
        <Button fullWidth onClick={onContinue}>
          continue
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
