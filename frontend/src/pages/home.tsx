import { Link, useNavigate } from "react-router-dom";
import { Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dashboard } from "@/components/Dashboard";
import {
  dismissFirstHint,
  isFirstHintDismissed,
} from "@/lib/ledger";
import { useDashboardSummary } from "@/lib/dashboardApi";
import { useAppStore } from "@/store";
import { track } from "@/lib/analytics";
import { cn } from "@/lib/utils";

const PREFILL_CHIPS = ["coffee $5.50", "lunch with M $24"];

export default function HomePage() {
  const navigate = useNavigate();
  const jwt = useAppStore((s) => s.jwt);
  const homeCurrency = useAppStore((s) => s.homeCurrency);
  const { summary, loading } = useDashboardSummary();
  const [hintDismissed, setHintDismissed] = useState(true);

  // Gate: redirect anyone who isn't fully onboarded to the wizard. We check
  // for missing JWT (signed out) or missing home_currency (signed in but
  // hasn't completed /auth/bootstrap yet). homeCurrency=undefined means /me
  // hasn't resolved yet — hold render until it does to avoid a flicker.
  const onboarded = !!jwt && typeof homeCurrency === "string";
  const shouldGate = !jwt || homeCurrency === null;

  useEffect(() => {
    if (shouldGate) {
      navigate("/onboarding", { replace: true });
      return;
    }
    setHintDismissed(isFirstHintDismissed());
    // feature_used: dashboard — fires once per page mount. StrictMode
    // double-mounts in dev produce two events; that's a dev-only
    // artifact, not a production concern.
    track("feature_used", { feature: "dashboard" });
  }, [navigate, shouldGate]);

  if (!onboarded) return null;

  // Hold the populated/empty branch until the first /dashboard/summary
  // round trip lands — otherwise we'd flash the empty state on every page
  // load before the response arrives.
  const { t } = useTranslation();

  if (!summary) {
    return (
      <div className="mx-auto w-full max-w-md px-5 pt-10 pb-12">
        <header>
          <h1 className="font-serif text-3xl text-ink lowercase-title">{t("home.title")}</h1>
        </header>
        {loading && (
          <p className="mt-12 text-sm text-ink-tertiary">{t("home.loading")}</p>
        )}
      </div>
    );
  }

  const isEmpty =
    !summary.baseline_ready && summary.categories.length === 0;

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-10 pb-12 animate-fade-up">
      {isEmpty ? (
        <EmptyHome
          showHint={!hintDismissed}
          onDismissHint={() => {
            dismissFirstHint();
            setHintDismissed(true);
          }}
        />
      ) : (
        <Dashboard data={summary} />
      )}
    </div>
  );
}

/* ─── Empty ──────────────────────────────────────────────────── */

function EmptyHome({
  showHint,
  onDismissHint,
}: {
  showHint: boolean;
  onDismissHint: () => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">{t("home.title")}</h1>
      </header>

      <div className="mt-20 flex flex-col items-center text-center">
        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-sunken text-ink-tertiary">
          <Wallet className="h-8 w-8" strokeWidth={1.5} />
        </div>
        <h2 className="mt-6 font-serif text-2xl text-ink lowercase-title">
          {t("home.emptyTitle")}
        </h2>
        <p className="mt-2 max-w-[26ch] text-sm text-ink-secondary">
          {t("home.emptyBody")}
        </p>
      </div>

      {/* Faded ghost tiles */}
      <div className="mt-12 grid grid-cols-2 gap-3 opacity-30">
        <GhostTile />
        <GhostTile />
      </div>

      {/* Pulsing ring around chat button (rendered via fixed pos to align with BottomNav) */}
      <ChatButtonPulse />

      {showHint && (
        <FirstHintStrip onDismiss={onDismissHint} />
      )}
    </>
  );
}

function GhostTile() {
  return (
    <div className="rounded-2xl border border-hairline bg-sunken/40 p-4 min-h-[6.5rem]">
      <div className="h-3 w-16 rounded-full bg-ink-quaternary/30" />
      <div className="mt-4 h-6 w-20 rounded-full bg-ink-quaternary/30" />
      <div className="mt-2 h-2 w-12 rounded-full bg-ink-quaternary/20" />
    </div>
  );
}

/**
 * A subtle pulse ring positioned to surround the BottomNav's center chat
 * button. The button itself is in BottomNav; this is a sibling halo.
 */
function ChatButtonPulse() {
  return (
    <div className="pointer-events-none fixed bottom-0 left-1/2 z-30 -translate-x-1/2 md:hidden">
      <div className="relative h-16 w-16 -translate-y-[1.7rem]">
        <span className="absolute inset-0 animate-ping-soft rounded-full bg-moss/30" />
      </div>
    </div>
  );
}

function FirstHintStrip({ onDismiss }: { onDismiss: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="pointer-events-auto fixed bottom-20 left-1/2 z-40 w-[min(92vw,22rem)] -translate-x-1/2 rounded-2xl border border-hairline bg-elevated px-4 py-3 md:hidden animate-fade-up">
      <p className="font-serif italic text-sm text-ink-secondary lowercase-title">
        {t("home.hintPrompt")}
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {PREFILL_CHIPS.map((chip) => (
          <Link
            key={chip}
            to="/chat"
            onClick={onDismiss}
            className={cn(
              "rounded-full border border-hairline bg-surface px-3 py-1 text-xs text-ink-secondary",
              "transition-colors hover:bg-sunken/60 hover:text-ink"
            )}
          >
            {chip}
          </Link>
        ))}
        <button
          type="button"
          onClick={onDismiss}
          className="ml-auto text-[0.7rem] text-ink-tertiary hover:text-ink-secondary"
        >
          {t("home.hintDismiss")}
        </button>
      </div>
    </div>
  );
}
