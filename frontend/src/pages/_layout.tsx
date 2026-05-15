import { Outlet, useLocation, Link } from "react-router-dom";
import { ThemeToggle } from "@/components/ThemeToggle";
import { BottomNav } from "@/components/BottomNav";
import { Sidebar } from "@/components/Sidebar";
import { DesktopComposer } from "@/components/desktop/DesktopComposer";
import { ChatDrawer } from "@/components/desktop/ChatDrawer";
import { CmdKTooltip } from "@/components/desktop/CmdKTooltip";

/*
 * Lovable's __root.tsx (TanStack Start) becomes a plain layout component.
 * The <head>/theme-color metadata that lived in `head:` is in
 * frontend/index.html. The TanStack shellComponent + HeadContent + Scripts
 * are SSR concerns that don't apply to our Vite SPA target.
 */
export default function Layout() {
  const pathname = useLocation().pathname;
  // Onboarding + chat are full-screen — no sidebar, no bottom nav.
  const isBare =
    pathname.startsWith("/onboarding") || pathname.startsWith("/chat");

  if (isBare) {
    return (
      <div className="min-h-screen bg-canvas text-ink">
        <ThemeToggle />
        <Outlet />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <ThemeToggle />
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="relative flex-1 pb-24 md:pb-32">
          <Outlet />
        </main>
      </div>
      <BottomNav />
      {/* Desktop-only persistent surfaces */}
      <DesktopComposer />
      <ChatDrawer />
      <CmdKTooltip />
    </div>
  );
}

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6">
      <div className="max-w-sm text-center">
        <h1 className="font-serif text-6xl text-ink lowercase-title">not found</h1>
        <p className="mt-3 text-sm text-ink-secondary">
          this page hasn't been built yet — or it never was.
        </p>
        <Link
          to="/"
          className="mt-6 inline-flex h-11 items-center justify-center rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
        >
          go home
        </Link>
      </div>
    </div>
  );
}
