import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-20 animate-fade-up">
      <h1 className="font-serif text-3xl text-ink lowercase-title">
        {t("onboarding.philosophy.title")}
      </h1>

      <div className="mt-8 flex flex-col gap-5 text-[0.95rem] leading-relaxed text-ink-secondary">
        <p>{t("onboarding.philosophy.p1")}</p>
        <p>{t("onboarding.philosophy.p2")}</p>
        <p>{t("onboarding.philosophy.p3")}</p>
        <p className="text-ink">{t("onboarding.philosophy.p4")}</p>
      </div>

      <div className="flex-1" />

      <div className="mt-10 flex flex-col items-center gap-4">
        <Button fullWidth onClick={onContinue}>
          {t("onboarding.philosophy.getStarted")}
        </Button>
        <button
          type="button"
          onClick={onTour}
          className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
        >
          {t("onboarding.philosophy.takeTour")}
        </button>
      </div>
    </div>
  );
}
