import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bell,
  ChevronRight,
  Download,
  Lock,
  Plug,
  Shield,
  Upload,
  User,
} from "lucide-react";
import { useAppStore } from "@/store";
import { cn } from "@/lib/utils";
import { ImportCsvSheet } from "@/components/ImportCsvSheet";
import {
  readPreferences,
  updatePreferences,
} from "@/lib/preferencesApi";
import { AnalyticsOptOutToggle } from "@/components/AnalyticsOptOutToggle";
import { DeleteAccountRow } from "@/components/DeleteAccountRow";
import { ExportDataButton } from "@/components/ExportDataButton";
import { PrivacyDisclosure } from "@/components/PrivacyDisclosure";
import { downloadUserDataExport } from "@/lib/exportApi";
import { track } from "@/lib/analytics";

/**
 * Render a currency code as "USD · $" using Intl. The home-currency invariant
 * (CLAUDE.md #13) means this is set once at signup; rendering the symbol next
 * to it keeps the account panel readable without a hand-maintained map.
 */
function currencyDisplay(code: string | null | undefined): string {
  if (!code) return "—";
  try {
    const parts = new Intl.NumberFormat("en", {
      style: "currency",
      currency: code,
      currencyDisplay: "narrowSymbol",
    }).formatToParts(0);
    const symbol = parts.find((p) => p.type === "currency")?.value;
    return symbol && symbol !== code ? `${code} · ${symbol}` : code;
  } catch {
    return code;
  }
}

type SectionId =
  | "account"
  | "connections"
  | "import"
  | "export"
  | "notifications"
  | "privacy";

interface Section {
  id: SectionId;
  label: string;
  icon: React.ReactNode;
  /** When true, renders as a Link to a separate route instead of an in-pane section. */
  href?: string;
}

const sections: Section[] = [
  { id: "account", label: "account", icon: <User className="h-4 w-4" /> },
  {
    id: "connections",
    label: "claude connections",
    icon: <Plug className="h-4 w-4" />,
    href: "/connections",
  },
  { id: "import", label: "import", icon: <Upload className="h-4 w-4" /> },
  { id: "export", label: "export", icon: <Download className="h-4 w-4" /> },
  {
    id: "notifications",
    label: "notifications",
    icon: <Bell className="h-4 w-4" />,
  },
  { id: "privacy", label: "privacy", icon: <Shield className="h-4 w-4" /> },
];

export default function SettingsPage() {
  const [active, setActive] = useState<SectionId>("account");

  return (
    <div className="mx-auto w-full max-w-5xl px-5 pt-8 pb-20">
      <h1 className="font-serif text-3xl text-ink lowercase-title md:hidden">
        settings
      </h1>

      {/* Mobile: simple list mirroring More menu's secondary section */}
      <ul className="mt-6 divide-y divide-hairline rounded-2xl border border-hairline bg-surface md:hidden">
        {sections.map((s) =>
          s.href ? (
            <li key={s.id}>
              <Link
                to={s.href}
                className="flex items-center gap-3 px-4 py-3.5 text-[0.95rem] text-ink hover:bg-elevated"
              >
                <span className="text-ink-tertiary">{s.icon}</span>
                <span className="flex-1 lowercase">{s.label}</span>
                <ChevronRight className="h-4 w-4 text-ink-quaternary" />
              </Link>
            </li>
          ) : (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => setActive(s.id)}
                className="flex w-full items-center gap-3 px-4 py-3.5 text-left text-[0.95rem] text-ink hover:bg-elevated"
              >
                <span className="text-ink-tertiary">{s.icon}</span>
                <span className="flex-1 lowercase">{s.label}</span>
                <ChevronRight className="h-4 w-4 text-ink-quaternary" />
              </button>
            </li>
          )
        )}
      </ul>

      {/* Mobile: render the active section's content below the list */}
      <div className="mt-6 md:hidden">
        <SectionContent id={active} />
      </div>

      {/* Desktop: macOS System Settings two-pane */}
      <div className="hidden md:flex md:gap-8 md:pt-2">
        <aside className="w-56 flex-shrink-0">
          <h1 className="px-3 font-serif text-2xl text-ink lowercase-title">
            settings
          </h1>
          <nav className="mt-5 flex flex-col gap-0.5">
            {sections.map((s) => {
              const baseCls = cn(
                "relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors",
                active === s.id && !s.href
                  ? "bg-sunken text-ink"
                  : "text-ink-secondary hover:bg-sunken/60 hover:text-ink"
              );
              if (s.href) {
                return (
                  <Link key={s.id} to={s.href} className={baseCls}>
                    {s.icon}
                    <span className="lowercase">{s.label}</span>
                  </Link>
                );
              }
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setActive(s.id)}
                  className={cn(baseCls, "text-left")}
                >
                  {s.icon}
                  <span className="lowercase">{s.label}</span>
                </button>
              );
            })}
          </nav>
        </aside>
        <main className="min-w-0 flex-1 border-l border-hairline pl-8">
          <SectionContent id={active} />
        </main>
      </div>
    </div>
  );
}

function SectionContent({ id }: { id: SectionId }) {
  if (id === "account") return <AccountPanel />;
  if (id === "import") return <ImportPanel />;
  if (id === "export") return <ExportPanel />;
  if (id === "notifications") return <NotificationsPanel />;
  if (id === "privacy") return <PrivacyPanel />;
  // connections is a Link, never rendered as a panel
  return null;
}

function PanelHeading({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <header className="mb-5">
      <h2 className="font-serif text-2xl text-ink lowercase-title">{title}</h2>
      {subtitle && (
        <p className="mt-1 text-sm text-ink-tertiary">{subtitle}</p>
      )}
    </header>
  );
}

function ReadonlyRow({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-hairline px-1 py-3 last:border-b-0">
      <span className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
        {label}
      </span>
      <div className="flex items-center justify-between gap-3">
        <span className="text-[0.95rem] text-ink">{value}</span>
        {note && (
          <span className="inline-flex items-center gap-1 text-[0.72rem] text-ink-tertiary">
            <Lock className="h-3 w-3" />
            {note}
          </span>
        )}
      </div>
    </div>
  );
}

function AccountPanel() {
  const email = useAppStore((s) => s.user?.email ?? "");
  const homeCurrency = useAppStore((s) => s.homeCurrency);
  return (
    <div>
      <PanelHeading title="account" subtitle="who you are." />
      <div className="rounded-2xl border border-hairline bg-surface px-4">
        <ReadonlyRow label="email" value={email} note="immutable" />
        <ReadonlyRow
          label="home currency"
          value={currencyDisplay(homeCurrency)}
          note="immutable"
        />
      </div>
      <p className="mt-3 px-1 text-[0.78rem] text-ink-tertiary">
        these can't be changed yet — by design. your home currency anchors
        every comparison and your email anchors your data.
      </p>
    </div>
  );
}

function ImportPanel() {
  const [sheetOpen, setSheetOpen] = useState(false);
  return (
    <div>
      <PanelHeading
        title="import"
        subtitle="bring transactions in from another tool."
      />
      <div className="rounded-2xl border border-hairline bg-surface px-4 py-4">
        <p className="text-[0.9rem] text-ink">
          drop a csv exported from your bank, ynab, or copilot.
        </p>
        <button
          type="button"
          onClick={() => setSheetOpen(true)}
          className="mt-3 inline-flex h-10 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-4 text-sm text-ink hover:bg-sunken"
          data-testid="open-import-csv"
        >
          <Upload className="h-4 w-4" />
          choose a csv
        </button>
        <p className="mt-3 text-[0.78rem] text-ink-tertiary">
          tameru detects the columns, categorizes in batches, and skips
          rows that already exist. up to 5 MB and 5,000 rows.
        </p>
      </div>
      <ImportCsvSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />
    </div>
  );
}

function ExportPanel() {
  // Day 27 — wires the JSON download to the shared /export route via
  // `downloadUserDataExport`. The Privacy panel also exposes the same
  // affordance via `ExportDataButton`; both surfaces converge on the
  // same backend. CSV export is out of v1 scope (DESIGN.md §9.6 — json
  // dump only).
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");

  const handleExport = async () => {
    if (status === "loading") return;
    setStatus("loading");
    try {
      await downloadUserDataExport();
      track("feature_used", { feature: "data_export" });
      setStatus("idle");
    } catch {
      track("error_shown", { code: "internal_error" });
      setStatus("error");
    }
  };

  return (
    <div>
      <PanelHeading
        title="export"
        subtitle="your data, when you want it."
      />
      <div className="rounded-2xl border border-hairline bg-surface px-4 py-4">
        <p className="text-[0.9rem] text-ink">
          download a json file containing your transactions, cards,
          subscriptions, chat history, memory facts, merchant overrides,
          and preferences.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleExport}
            disabled={status === "loading"}
            aria-busy={status === "loading"}
            className={cn(
              "inline-flex h-10 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-4 text-sm text-ink hover:bg-sunken",
              status === "loading" && "opacity-50 cursor-not-allowed",
            )}
            data-testid="settings-export-json"
          >
            <Download className="h-4 w-4" />
            {status === "loading" ? "preparing…" : "export json"}
          </button>
        </div>
        {status === "error" && (
          <p
            role="alert"
            className="mt-3 text-[0.78rem] text-over"
          >
            couldn't prepare your export. try again in a moment.
          </p>
        )}
      </div>
    </div>
  );
}

function NotificationsPanel() {
  // Server-backed (Day 25, DESIGN.md §6.4). Optimistic UI: flip locally,
  // then PATCH and reconcile against the server's returned canonical
  // value. The /unsubscribe route and the Resend bounce webhook also
  // flip this same boolean, so a fresh mount re-reads the server state
  // in case it changed since this tab was last open.
  const [weekly, setWeekly] = useState(true);
  const [savingWeekly, setSavingWeekly] = useState(false);
  const [overspend, setOverspend] = useState(true);
  const [quiet, setQuiet] = useState(false);

  // Monotonic request sequence. Each PATCH increments it; only the
  // response whose sequence matches the latest in-flight value is
  // allowed to mutate `weekly`. Without this guard, rapid toggling
  // could let an older PATCH's response land after a newer one's and
  // leave the UI (and the server, if the older PATCH wins the race on
  // its side too) showing the stale value. Codex 2026-05-23 P2.
  const latestRequest = useRef(0);

  useEffect(() => {
    // Tie the mount-time read into the same monotonic sequence as the
    // PATCH path. Without this, a slow read resolving *after* the
    // user's first toggle would overwrite the persisted value with the
    // stale pre-toggle one (Codex 2026-05-23 P2).
    const myRequest = ++latestRequest.current;
    readPreferences()
      .then((prefs) => {
        if (myRequest !== latestRequest.current) return;
        setWeekly(prefs.weekly_digest_enabled);
      })
      .catch(() => {
        // Network failure: keep the optimistic default; the user can
        // still toggle, and the next PATCH will reconcile.
      });
  }, []);

  const handleWeeklyChange = (next: boolean) => {
    // Defense in depth: ToggleRow's `disabled` prop already blocks
    // clicks while saving. This guard catches any path that bypasses
    // the prop (programmatic call, future caller).
    if (savingWeekly) return;
    setWeekly(next);
    setSavingWeekly(true);
    const myRequest = ++latestRequest.current;
    updatePreferences({ weekly_digest_enabled: next })
      .then((prefs) => {
        if (myRequest !== latestRequest.current) return;
        setWeekly(prefs.weekly_digest_enabled);
      })
      .catch(() => {
        if (myRequest !== latestRequest.current) return;
        // Revert the latest user choice on failure so the UI doesn't
        // lie about persisted state.
        setWeekly(!next);
      })
      .finally(() => {
        if (myRequest !== latestRequest.current) return;
        setSavingWeekly(false);
      });
  };

  return (
    <div>
      <PanelHeading
        title="notifications"
        subtitle="tameru speaks softly. you choose how often."
      />
      <div className="divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <ToggleRow
          label="weekly digest email"
          desc="a quiet recap every monday morning. unsubscribe link in every send."
          checked={weekly}
          onChange={handleWeeklyChange}
          disabled={savingWeekly}
        />
        <ToggleRow
          label="overspend nudge"
          desc="ping when a category goes well past its usual."
          checked={overspend}
          onChange={setOverspend}
        />
        <ToggleRow
          label="quiet mode"
          desc="hold all notifications until you next open the app."
          checked={quiet}
          onChange={setQuiet}
        />
      </div>
    </div>
  );
}

function PrivacyPanel() {
  // Day 27 — same component stack as `/privacy` (the mobile-reachable
  // route). Extracting the shared components keeps the desktop and
  // mobile surfaces in lockstep so a copy or wiring change only has to
  // land in one place. Optimistic-write + monotonic-sequence + SDK
  // lockstep for the opt-out toggle live in AnalyticsOptOutToggle
  // itself (Day 26).
  return (
    <div>
      <PanelHeading
        title="privacy"
        subtitle="what tameru does with your data, and what it doesn't."
      />
      <div className="divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <AnalyticsOptOutToggle />
        <ExportDataButton />
        <DeleteAccountRow />
      </div>
      <div className="mt-6">
        <PrivacyDisclosure />
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  desc,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <p className="text-[0.95rem] text-ink lowercase-title">{label}</p>
        <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">{desc}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-disabled={disabled}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-6 w-10 flex-shrink-0 rounded-full transition-colors",
          checked ? "bg-moss" : "bg-sunken",
          // Disabled-while-saving treatment: dim + not-allowed cursor.
          // Defense-in-depth alongside the `disabled` attribute, which
          // already blocks pointer events.
          disabled && "opacity-50 cursor-not-allowed"
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-surface shadow transition-all",
            checked ? "left-[1.125rem]" : "left-0.5"
          )}
        />
      </button>
    </div>
  );
}
