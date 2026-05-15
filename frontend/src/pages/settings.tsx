import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Bell,
  ChevronRight,
  Download,
  Lock,
  Plug,
  Upload,
  User,
} from "lucide-react";
import { useAppStore } from "@/store";
import { cn } from "@/lib/utils";

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
  | "notifications";

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
          className="mt-3 inline-flex h-10 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-4 text-sm text-ink hover:bg-sunken"
        >
          <Upload className="h-4 w-4" />
          choose a csv
        </button>
      </div>
    </div>
  );
}

function ExportPanel() {
  return (
    <div>
      <PanelHeading
        title="export"
        subtitle="your data, when you want it."
      />
      <div className="rounded-2xl border border-hairline bg-surface px-4 py-4">
        <p className="text-[0.9rem] text-ink">
          download a complete copy of your ledger — transactions, cards, and
          subscriptions.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="inline-flex h-10 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-4 text-sm text-ink hover:bg-sunken"
          >
            <Download className="h-4 w-4" />
            export csv
          </button>
          <button
            type="button"
            className="inline-flex h-10 items-center gap-2 rounded-2xl border border-hairline bg-elevated px-4 text-sm text-ink hover:bg-sunken"
          >
            <Download className="h-4 w-4" />
            export json
          </button>
        </div>
      </div>
    </div>
  );
}

function NotificationsPanel() {
  const [weekly, setWeekly] = useState(true);
  const [overspend, setOverspend] = useState(true);
  const [quiet, setQuiet] = useState(false);

  return (
    <div>
      <PanelHeading
        title="notifications"
        subtitle="tameru speaks softly. you choose how often."
      />
      <div className="divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <ToggleRow
          label="weekly summary"
          desc="a quiet recap every sunday morning."
          checked={weekly}
          onChange={setWeekly}
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

function ToggleRow({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
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
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-6 w-10 flex-shrink-0 rounded-full transition-colors",
          checked ? "bg-moss" : "bg-sunken"
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
