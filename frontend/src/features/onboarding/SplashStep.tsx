import { Button } from "@/components/Button";
import { SketchIcon } from "@/components/SketchIcon";

interface SplashStepProps {
  onContinue: () => void;
  onTour: () => void;
}

export function SplashStep({ onContinue, onTour }: SplashStepProps) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-between px-6 pb-12 pt-24 animate-fade-up">
      <div className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
        <div className="relative flex flex-col items-center">
          <SketchIcon
            kind="seedling"
            size={36}
            seed={3}
            className="absolute -top-6 text-moss"
          />
          <span
            className="font-serif text-[10rem] leading-none text-moss-deep"
            aria-hidden="true"
          >
            貯
          </span>
        </div>
        <div className="flex flex-col items-center gap-2">
          <h1 className="font-serif text-5xl text-ink lowercase-title">tameru</h1>
          <p className="font-serif italic text-ink-secondary text-base">
            the mindful ledger
          </p>
        </div>
      </div>

      <div className="flex w-full max-w-sm flex-col items-center gap-4">
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
