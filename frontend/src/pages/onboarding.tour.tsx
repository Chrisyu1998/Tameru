import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Pill } from "@/components/Pill";
import { DeltaTile } from "@/components/DeltaTile";
import { cn } from "@/lib/utils";
import { markOnboarded } from "@/lib/onboarding";

interface TourScreen {
  title: string;
  callout: string;
  illustration: React.ReactNode;
}

function DashboardIllustration() {
  return (
    <div className="flex flex-col gap-3">
      <Card variant="elevated">
        <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          this week
        </p>
        <p className="mt-2 font-serif text-3xl text-ink tabular">$412</p>
        <p className="mt-1 text-xs text-ink-tertiary">3 days into the week</p>
      </Card>
      <div className="flex flex-col gap-2">
        <DeltaTile category="dining" delta={47} />
        <DeltaTile category="groceries" delta={-22} />
        <DeltaTile category="transit" delta={8} />
      </div>
    </div>
  );
}

function EntryNudgeIllustration() {
  return (
    <Card variant="elevated" className="border-moss-soft/40 bg-moss-wash/30">
      <div className="flex items-start gap-3">
        <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-moss" />
        <div className="flex flex-col gap-2">
          <p className="font-serif italic text-ink-secondary lowercase-title">
            a small ritual, twice a day.
          </p>
          <p className="text-sm text-ink">
            log what you bought. one line, one breath.
          </p>
          <div className="mt-2 flex items-center gap-2 rounded-2xl border border-hairline bg-surface px-3 py-2">
            <span className="text-xs text-ink-tertiary">8:14am ·</span>
            <span className="text-sm text-ink">flat white</span>
            <span className="ml-auto tabular text-sm text-ink">$5.50</span>
          </div>
        </div>
      </div>
    </Card>
  );
}

function ChatIllustration() {
  return (
    <div className="flex flex-col gap-3">
      <div className="self-end max-w-[80%] rounded-2xl rounded-tr-sm border border-hairline bg-elevated px-4 py-2.5 text-sm text-ink">
        why was last week so high?
      </div>
      <div className="self-start max-w-[85%] rounded-2xl rounded-tl-sm bg-moss-wash px-4 py-2.5 text-sm text-moss-deep">
        Two restaurants and a flight. Without those, you spent $54 less than
        usual.
      </div>
      <div className="self-end max-w-[60%] rounded-2xl rounded-tr-sm border border-hairline bg-elevated px-4 py-2.5 text-sm text-ink">
        thanks. that helps.
      </div>
    </div>
  );
}

function DigestIllustration() {
  return (
    <Card variant="elevated">
      <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        sunday digest
      </p>
      <h3 className="mt-2 font-serif text-xl text-ink lowercase-title">
        a quiet week.
      </h3>
      <ul className="mt-4 flex flex-col gap-2.5 text-sm text-ink-secondary">
        <li className="flex items-start gap-2">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-moss" />
          spent <span className="tabular text-ink">$284</span>, below your
          usual $340.
        </li>
        <li className="flex items-start gap-2">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-moss" />
          dining is trending down for the second week.
        </li>
        <li className="flex items-start gap-2">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-warn" />
          one subscription renews tuesday.
        </li>
      </ul>
    </Card>
  );
}

const SCREENS: TourScreen[] = [
  {
    title: "your week, at a glance",
    callout: "spending is shown as deltas — never absolute totals.",
    illustration: <DashboardIllustration />,
  },
  {
    title: "a small ritual",
    callout: "manual entry keeps you present with your money.",
    illustration: <EntryNudgeIllustration />,
  },
  {
    title: "ask in plain language",
    callout: "your ledger talks back, kindly and in context.",
    illustration: <ChatIllustration />,
  },
  {
    title: "the sunday digest",
    callout: "one calm summary per week. nothing more.",
    illustration: <DigestIllustration />,
  },
];

export default function TourPage() {
  const navigate = useNavigate();
  const [idx, setIdx] = useState(0);
  const screen = SCREENS[idx];
  const isLast = idx === SCREENS.length - 1;

  const finishTour = () => {
    markOnboarded();
    navigate("/");
  };

  const next = () => {
    if (isLast) finishTour();
    else setIdx((i) => i + 1);
  };

  const back = () => {
    if (idx === 0) navigate("/onboarding");
    else setIdx((i) => i - 1);
  };

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-16">
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

      <div className="mt-10 flex flex-col items-center gap-6">
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

        <Button fullWidth size="lg" onClick={next}>
          {isLast ? "log your first transaction →" : "next"}
        </Button>

        {!isLast && (
          <button
            type="button"
            onClick={finishTour}
            className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
          >
            skip the tour
          </button>
        )}
      </div>
    </div>
  );
}
