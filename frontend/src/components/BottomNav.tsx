import { Link, useLocation } from "react-router-dom";
import { SketchIcon } from "@/components/SketchIcon";
import { cn } from "@/lib/utils";

export function BottomNav() {
  const pathname = useLocation().pathname;

  const isActive = (path: string) =>
    path === "/" ? pathname === "/" : pathname.startsWith(path);

  return (
    <nav
      aria-label="primary"
      className="fixed bottom-0 left-0 right-0 z-40 border-t border-hairline bg-canvas/90 backdrop-blur md:hidden"
    >
      <div className="relative mx-auto flex h-16 max-w-md items-center justify-between px-10">
        <NavItem
          to="/"
          label="home"
          active={isActive("/")}
          icon={<SketchIcon kind="home" size={22} seed={11} />}
        />

        {/* Center raised chat button — sketched bubble on moss circle */}
        <Link
          to="/chat"
          aria-label="chat"
          className={cn(
            "absolute left-1/2 top-0 -translate-x-1/2 -translate-y-1/3",
            "flex h-12 w-12 items-center justify-center rounded-full border border-hairline",
            "bg-moss-deep text-surface transition-colors hover:bg-moss"
          )}
        >
          <SketchIcon kind="chat-bubble" size={22} seed={29} amp={0.35} />
        </Link>

        <NavItem
          to="/more"
          label="more"
          active={isActive("/more")}
          icon={<SketchIcon kind="dots" size={22} seed={41} />}
        />
      </div>
      <div className="h-[env(safe-area-inset-bottom)]" />
    </nav>
  );
}

function NavItem({
  to,
  label,
  icon,
  active,
}: {
  to: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
}) {
  return (
    <Link
      to={to}
      className={cn(
        "flex flex-col items-center gap-0.5 text-[0.7rem] font-medium tracking-wide",
        active ? "text-moss" : "text-ink-tertiary"
      )}
    >
      {icon}
      <span className="lowercase">{label}</span>
    </Link>
  );
}
