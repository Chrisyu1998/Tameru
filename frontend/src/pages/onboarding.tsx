import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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

/** Steps the `?step=` deep link accepts. Anything else is ignored. */
const DEEP_LINK_STEPS = new Set<OnboardingStep>([
  "signin",
  "addCard",
  "csvImport",
]);

/**
 * Pick where the wizard should open. Cases:
 *   - `?step=csvImport` (Tour's final "import a CSV" CTA):
 *       - authed + bootstrapped → jump straight to csvImport
 *       - authed but no home_currency → currency (then flows forward to csvImport)
 *       - not authed → signin (then flows forward to csvImport)
 *   - `?step=signin` (Tour's final "log my first transaction" CTA, unauthed):
 *       - not authed → signin
 *   - no query param:
 *       - authed + no home_currency → currency (post-OAuth landing)
 *       - else → splash (full first-launch flow)
 */
function pickStartStep(
  hasJwt: boolean,
  homeCurrency: string | null | undefined,
  requested: OnboardingStep | null,
): OnboardingStep {
  if (requested === "csvImport") {
    if (hasJwt && typeof homeCurrency === "string") return "csvImport";
    if (hasJwt && homeCurrency == null) return "currency";
    return "signin";
  }
  if (requested === "signin" && !hasJwt) return "signin";
  if (hasJwt && homeCurrency == null) return "currency";
  return "splash";
}

export default function OnboardingWizard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const jwt = useAppStore((s) => s.jwt);
  const homeCurrency = useAppStore((s) => s.homeCurrency);

  const requestedRaw = searchParams.get("step") as OnboardingStep | null;
  const requested =
    requestedRaw && DEEP_LINK_STEPS.has(requestedRaw) ? requestedRaw : null;

  const [step, setStep] = useState<OnboardingStep>(() =>
    pickStartStep(!!jwt, homeCurrency, requested),
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
  // wizard. Skipped when an explicit `?step=` deep link is present — that
  // signal comes from the tour's final CTAs and wants the wizard, not a
  // re-tour.
  useEffect(() => {
    if (requested) return;
    if (jwt && typeof homeCurrency === "string") {
      navigate("/onboarding/tour", { replace: true });
    }
  }, [jwt, homeCurrency, navigate, requested]);

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
