import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Dashboard } from "@/components/Dashboard";
import { ChatThread } from "@/components/chat/ChatThread";
import { Pill } from "@/components/Pill";
import { EntryNudgeAnimation } from "@/components/tour/EntryNudgeAnimation";
import { DigestEmailPreview } from "@/components/tour/DigestEmailPreview";
import { tourFixtures } from "@/fixtures/tour";
import { useAppStore } from "@/store";
import { markOnboarded } from "@/lib/onboarding";
import { track } from "@/lib/analytics";
import { cn } from "@/lib/utils";

/**
 * Screen title + callout keys — module-scope so they're not recreated on
 * every render. Resolved inside the component with `t()`.
 */
const SCREEN_KEYS = [
  {
    titleKey: "tour.screen1.title",
    calloutKey: "tour.screen1.callout",
  },
  {
    titleKey: "tour.screen2.title",
    calloutKey: "tour.screen2.callout",
  },
  {
    titleKey: "tour.screen3.title",
    calloutKey: "tour.screen3.callout",
  },
  {
    titleKey: "tour.screen4.title",
    calloutKey: "tour.screen4.callout",
  },
] as const;

/** Illustrations are static and don't depend on i18n. */
const ILLUSTRATIONS = [
  (
    <div className="mx-auto w-full max-w-md">
      <Dashboard data={tourFixtures.dashboard} inert />
    </div>
  ),
  <EntryNudgeAnimation />,
  <ChatThread messages={tourFixtures.chat} />,
  <DigestEmailPreview />,
];

export default function TourPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [idx, setIdx] = useState(0);
  const screenKeys = SCREEN_KEYS[idx];
  const isLast = idx === SCREEN_KEYS.length - 1;
  const jwt = useAppStore((s) => s.jwt);
  const homeCurrency = useAppStore((s) => s.homeCurrency);
  const fullyOnboarded = !!jwt && typeof homeCurrency === "string";

  // Final-CTA targets:
  //   - "import a CSV": always deep-links into the wizard's csvImport step.
  //     The wizard's pickStartStep handles every auth state — fully authed
  //     jumps straight in; unauthed flows through signin → currency →
  //     addCard → csvImport.
  //   - "log my first transaction": authed users land on `/` (their real
  //     home); unauthed users get the signin deep link so they don't
  //     bounce back to splash.
  const importCsvCta = () => {
    track("onboarding_step_completed", { step: "tourCompleted" });
    navigate("/onboarding?step=csvImport");
  };
  const logFirstCta = () => {
    track("onboarding_step_completed", { step: "tourCompleted" });
    if (fullyOnboarded) {
      markOnboarded();
      navigate("/");
    } else {
      navigate("/onboarding?step=signin");
    }
  };

  const next = () => {
    if (isLast) return; // final screen renders dual CTAs, not "next"
    setIdx((i) => i + 1);
  };

  const back = () => {
    if (idx === 0) navigate("/onboarding");
    else setIdx((i) => i - 1);
  };

  const totalScreens = SCREEN_KEYS.length;

  // Horizontal swipe gesture. Threshold of 50px is the conventional
  // sweet spot — large enough to ignore accidental drift, small enough
  // that an intentional flick clears it. Vertical-dominant motion is
  // ignored so the user can still scroll within a screen.
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart: React.TouchEventHandler = (e) => {
    const touch = e.touches[0];
    touchStart.current = { x: touch.clientX, y: touch.clientY };
  };
  const onTouchEnd: React.TouchEventHandler = (e) => {
    const start = touchStart.current;
    touchStart.current = null;
    if (!start) return;
    const touch = e.changedTouches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) next();
    else back();
  };

  return (
    <div
      className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-16"
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={back}
          aria-label={t("tour.back")}
          className="flex h-10 w-10 items-center justify-center rounded-full text-ink-secondary transition-colors hover:bg-sunken/60 hover:text-ink"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <Pill tone="warn">{t("tour.sampleData")}</Pill>
        <div className="w-10" />
      </div>

      <div key={idx} className="mt-10 animate-fade-up">
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          {t(screenKeys.titleKey)}
        </h1>
        <p className="mt-2 text-sm text-ink-secondary">{t(screenKeys.calloutKey)}</p>

        <div className="mt-8">{ILLUSTRATIONS[idx]}</div>
      </div>

      <div className="flex-1" />

      <div className="mt-10 flex flex-col items-center gap-5">
        <div className="flex items-center gap-2">
          {Array.from({ length: totalScreens }, (_, i) => (
            <span
              key={i}
              className={cn(
                "h-1.5 rounded-full transition-all",
                i === idx ? "w-6 bg-moss" : "w-1.5 bg-ink-quaternary/40"
              )}
            />
          ))}
        </div>

        {!isLast && (
          <>
            <Button fullWidth size="lg" onClick={next}>
              {t("tour.next")}
            </Button>
            <button
              type="button"
              onClick={logFirstCta}
              className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
            >
              {t("tour.skipTour")}
            </button>
          </>
        )}

        {isLast && (
          <>
            <p className="max-w-[28ch] text-center text-xs text-ink-tertiary">
              {t("tour.lastScreenHint")}
            </p>
            <Button fullWidth size="lg" onClick={importCsvCta}>
              {t("tour.importCsv")}
            </Button>
            <button
              type="button"
              onClick={logFirstCta}
              className="text-sm text-ink-secondary underline-offset-4 hover:text-ink hover:underline"
            >
              {t("tour.logFirstTransaction")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
