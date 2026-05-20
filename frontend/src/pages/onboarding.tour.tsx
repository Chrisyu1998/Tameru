import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/Button";
import { Dashboard } from "@/components/Dashboard";
import { ChatThread } from "@/components/chat/ChatThread";
import { Pill } from "@/components/Pill";
import { EntryNudgeAnimation } from "@/components/tour/EntryNudgeAnimation";
import { DigestEmailPreview } from "@/components/tour/DigestEmailPreview";
import { tourFixtures } from "@/fixtures/tour";
import { useAppStore } from "@/store";
import { markOnboarded } from "@/lib/onboarding";
import { cn } from "@/lib/utils";

interface TourScreen {
  title: string;
  callout: string;
  illustration: React.ReactNode;
}

const SCREENS: TourScreen[] = [
  {
    title: "your week, at a glance",
    callout: "spending is shown as deltas — never absolute totals.",
    illustration: (
      <div className="mx-auto w-full max-w-md">
        <Dashboard data={tourFixtures.dashboard} inert />
      </div>
    ),
  },
  {
    title: "a small ritual",
    callout: "log it, confirm it, hear the quiet observation.",
    illustration: <EntryNudgeAnimation />,
  },
  {
    title: "ask in plain language",
    callout: "your ledger talks back, kindly and in context.",
    illustration: <ChatThread messages={tourFixtures.chat} />,
  },
  {
    title: "the sunday digest",
    callout: "one calm summary per week. nothing more.",
    illustration: <DigestEmailPreview />,
  },
];

export default function TourPage() {
  const navigate = useNavigate();
  const [idx, setIdx] = useState(0);
  const screen = SCREENS[idx];
  const isLast = idx === SCREENS.length - 1;
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
  const importCsvCta = () => navigate("/onboarding?step=csvImport");
  const logFirstCta = () => {
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

  // Horizontal swipe gesture. Threshold of 50px is the conventional
  // sweet spot — large enough to ignore accidental drift, small enough
  // that an intentional flick clears it. Vertical-dominant motion is
  // ignored so the user can still scroll within a screen.
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart: React.TouchEventHandler = (e) => {
    const t = e.touches[0];
    touchStart.current = { x: t.clientX, y: t.clientY };
  };
  const onTouchEnd: React.TouchEventHandler = (e) => {
    const start = touchStart.current;
    touchStart.current = null;
    if (!start) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
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
          aria-label="back"
          className="flex h-10 w-10 items-center justify-center rounded-full text-ink-secondary transition-colors hover:bg-sunken/60 hover:text-ink"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <Pill tone="warn">SAMPLE DATA</Pill>
        <div className="w-10" />
      </div>

      <div key={idx} className="mt-10 animate-fade-up">
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          {screen.title}
        </h1>
        <p className="mt-2 text-sm text-ink-secondary">{screen.callout}</p>

        <div className="mt-8">{screen.illustration}</div>
      </div>

      <div className="flex-1" />

      <div className="mt-10 flex flex-col items-center gap-5">
        <div className="flex items-center gap-2">
          {SCREENS.map((_, i) => (
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
              next
            </Button>
            <button
              type="button"
              onClick={logFirstCta}
              className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
            >
              skip the tour
            </button>
          </>
        )}

        {isLast && (
          <>
            <p className="max-w-[28ch] text-center text-xs text-ink-tertiary">
              this is tameru with 3 months of data. log your first transaction
              or import a csv to get there.
            </p>
            <Button fullWidth size="lg" onClick={importCsvCta}>
              import a csv
            </Button>
            <button
              type="button"
              onClick={logFirstCta}
              className="text-sm text-ink-secondary underline-offset-4 hover:text-ink hover:underline"
            >
              log my first transaction
            </button>
          </>
        )}
      </div>
    </div>
  );
}
