import { Link, useNavigate, useLocation } from "react-router-dom";
import { Eraser, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import { SketchIcon } from "@/components/SketchIcon";
import { cn } from "@/lib/utils";
import { resetOnboarded } from "@/lib/onboarding";
import { ledger, useLedger } from "@/lib/ledger";

type Item = { to: string; label: string; icon: ReactNode };

const mainItems: Item[] = [
  { to: "/", label: "home", icon: <SketchIcon kind="home" size={18} seed={11} /> },
  { to: "/cards", label: "my cards", icon: <SketchIcon kind="card" size={18} seed={23} /> },
  { to: "/subscriptions", label: "subscriptions", icon: <SketchIcon kind="repeat" size={18} seed={37} /> },
  { to: "/memory", label: "ai memory", icon: <SketchIcon kind="sparkle" size={18} seed={53} /> },
];

const footerItems: Item[] = [
  { to: "/settings", label: "settings", icon: <SketchIcon kind="settings" size={18} seed={67} /> },
];

export function Sidebar() {
  const pathname = useLocation().pathname;
  const navigate = useNavigate();
  const { transactions } = useLedger();
  const isActive = (path: string) => (path === "/" ? pathname === "/" : pathname.startsWith(path));

  const restartOnboarding = () => {
    resetOnboarded();
    navigate("/onboarding");
  };

  const isEmpty = transactions.length === 0;
  const toggleLedger = () => {
    if (isEmpty) ledger.resetToFixtures();
    else ledger.clear();
  };

  return (
    <aside className="hidden md:flex h-screen w-60 flex-col bg-canvas">
      <div className="flex items-center gap-2 px-6 pt-7 pb-8">
        <SketchIcon kind="seedling" size={18} seed={7} className="text-moss" />
        <span className="font-serif text-2xl text-ink lowercase-title">tameru</span>
      </div>

      <nav className="flex flex-1 flex-col px-3">
        <ul className="flex flex-col gap-0.5">
          {mainItems.map((item) => (
            <SidebarLink key={item.to} item={item} active={isActive(item.to)} />
          ))}
        </ul>

        <div className="my-4" />

        <ul className="flex flex-col gap-0.5">
          {footerItems.map((item) => (
            <SidebarLink key={item.to} item={item} active={isActive(item.to)} />
          ))}
        </ul>

        <div className="flex-1" />

        <button
          type="button"
          onClick={toggleLedger}
          className="mx-1 flex items-center gap-3 rounded-xl px-3 py-2 text-left text-sm text-ink-tertiary transition-colors hover:text-ink-secondary"
        >
          <Eraser className="h-4 w-4" />
          <span className="lowercase">{isEmpty ? "restore sample data" : "clear ledger"}</span>
        </button>

        <button
          type="button"
          onClick={restartOnboarding}
          className="mx-1 mb-2 flex items-center gap-3 rounded-xl px-3 py-2 text-left text-sm text-ink-tertiary transition-colors hover:text-ink-secondary"
        >
          <RotateCcw className="h-4 w-4" />
          <span className="lowercase">restart onboarding</span>
        </button>

        <div className="mb-6 mt-2 flex items-center gap-3 px-3 py-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-moss-wash text-moss-deep font-serif">
            t
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm text-ink">guest</span>
            <span className="text-xs text-ink-tertiary">not signed in</span>
          </div>
        </div>
      </nav>
    </aside>
  );
}

function SidebarLink({ item, active }: { item: Item; active: boolean }) {
  return (
    <li>
      <Link
        to={item.to}
        className={cn(
          "relative flex items-center gap-3 rounded-none px-4 py-2 text-sm transition-colors",
          active
            ? "font-serif font-semibold text-ink lowercase-title"
            : "text-ink-tertiary hover:text-ink-secondary"
        )}
      >
        {active && (
          <span className="absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-full bg-moss" />
        )}
        <span className={cn("inline-flex", active && "text-moss")}>{item.icon}</span>
        <span className="lowercase">{item.label}</span>
      </Link>
    </li>
  );
}
