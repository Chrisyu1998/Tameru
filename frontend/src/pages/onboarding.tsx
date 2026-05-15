import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { SplashStep } from "@/features/onboarding/SplashStep";
import { PhilosophyStep } from "@/features/onboarding/PhilosophyStep";
import { SignInStep } from "@/features/onboarding/SignInStep";
import { CurrencyStep } from "@/features/onboarding/CurrencyStep";
import { AddCardStep } from "@/features/onboarding/AddCardStep";
import { CsvImportStep } from "@/features/onboarding/CsvImportStep";
import { CsvProcessingStep } from "@/features/onboarding/CsvProcessingStep";
import { OnboardingHeader } from "@/features/onboarding/OnboardingHeader";
import { markOnboarded } from "@/lib/onboarding";
import { useAppStore } from "@/store";
import type { Currency, OnboardingStep } from "@/features/onboarding/types";

const STEP_ORDER: OnboardingStep[] = [
  "splash",
  "philosophy",
  "signin",
  "currency",
  "addCard",
  "csvImport",
  "csvProcessing",
];

/** Steps where a back button is hidden (irreversible or in-progress). */
const NO_BACK: OnboardingStep[] = ["splash", "currency", "csvProcessing"];

/**
 * Pick where the wizard should open. Three cases:
 *   - no JWT → start at splash (full onboarding flow)
 *   - JWT + no home_currency → skip past signin to the currency step (this
 *     is the post-OAuth landing)
 *   - JWT + home_currency → wizard shouldn't be visible at all; the home
 *     gate routes elsewhere. We still default to splash so refreshing the
 *     URL directly doesn't crash.
 */
function pickStartStep(
  hasJwt: boolean,
  homeCurrency: string | null | undefined,
): OnboardingStep {
  if (hasJwt && homeCurrency == null) return "currency";
  return "splash";
}

export default function OnboardingWizard() {
  const navigate = useNavigate();
  const jwt = useAppStore((s) => s.jwt);
  const homeCurrency = useAppStore((s) => s.homeCurrency);
  const [step, setStep] = useState<OnboardingStep>(() =>
    pickStartStep(!!jwt, homeCurrency),
  );
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_currency, setCurrency] = useState<Currency>("USD");
  const [csvFilename, setCsvFilename] = useState<string>("");

  // If the user signs in *while sitting on splash/philosophy/signin* (e.g.
  // magic-link landing redirects back), jump them forward to the currency
  // step. We only fire this once per JWT acquisition so an in-progress
  // wizard doesn't get yanked around mid-step.
  useEffect(() => {
    if (jwt && homeCurrency == null) {
      setStep((current) =>
        current === "splash" || current === "philosophy" || current === "signin"
          ? "currency"
          : current,
      );
    }
  }, [jwt, homeCurrency]);

  // Fully-onboarded users who land on /onboarding (typically via the
  // sidebar's "restart onboarding") get the visual tour, not the auth
  // wizard — they're already authed and bootstrapped.
  useEffect(() => {
    if (jwt && typeof homeCurrency === "string") {
      navigate("/onboarding/tour", { replace: true });
    }
  }, [jwt, homeCurrency, navigate]);

  const goTo = (next: OnboardingStep) => setStep(next);
  const finish = () => {
    markOnboarded();
    navigate("/");
  };
  const goTour = () => navigate("/onboarding/tour");

  const back = () => {
    const idx = STEP_ORDER.indexOf(step);
    if (idx > 0) setStep(STEP_ORDER[idx - 1]);
  };

  const showBack = !NO_BACK.includes(step);

  return (
    <>
      <OnboardingHeader onBack={showBack ? back : undefined} />

      {step === "splash" && (
        <SplashStep onContinue={() => goTo("philosophy")} onTour={goTour} />
      )}

      {step === "philosophy" && (
        <PhilosophyStep onContinue={() => goTo("signin")} onTour={goTour} />
      )}

      {step === "signin" && <SignInStep onContinue={() => goTo("currency")} />}

      {step === "currency" && (
        <CurrencyStep
          onConfirm={(c) => {
            setCurrency(c);
            goTo("addCard");
          }}
        />
      )}

      {step === "addCard" && (
        <AddCardStep
          onSaved={() => goTo("csvImport")}
          onSkip={() => goTo("csvImport")}
        />
      )}

      {step === "csvImport" && (
        <CsvImportStep
          onContinue={(name) => {
            setCsvFilename(name);
            goTo("csvProcessing");
          }}
          onSkip={finish}
        />
      )}

      {step === "csvProcessing" && (
        <CsvProcessingStep filename={csvFilename} onComplete={finish} />
      )}
    </>
  );
}
