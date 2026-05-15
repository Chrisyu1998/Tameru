import { useEffect, useState } from "react";

const STORAGE_KEY = "tameru:cmdk-tooltip-shown";

/**
 * One-time tooltip shown the very first time the user lands on a desktop
 * non-bare screen. Persisted in localStorage so it never shows again.
 */
export function CmdKTooltip() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.matchMedia("(max-width: 767px)").matches) return;
    try {
      if (window.localStorage.getItem(STORAGE_KEY)) return;
    } catch {
      return;
    }
    const t = window.setTimeout(() => setVisible(true), 1200);
    return () => window.clearTimeout(t);
  }, []);

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    setVisible(false);
  };

  // Auto-dismiss on first ⌘K, too.
  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible]);

  if (!visible) return null;

  return (
    <div className="pointer-events-none fixed bottom-24 left-1/2 z-40 hidden -translate-x-1/2 md:block animate-fade-up">
      <div className="pointer-events-auto flex items-center gap-3 rounded-2xl border border-hairline bg-elevated px-4 py-2.5 text-[0.82rem] text-ink shadow-[0_6px_24px_-18px_rgba(0,0,0,0.18)]">
        <kbd className="inline-flex h-6 select-none items-center rounded-md border border-hairline bg-canvas px-2 text-[0.7rem] text-ink-secondary">
          ⌘K
        </kbd>
        <span className="lowercase">opens tameru ai from anywhere</span>
        <button
          type="button"
          onClick={dismiss}
          className="ml-1 text-[0.72rem] text-ink-tertiary hover:text-ink"
        >
          got it
        </button>
      </div>
    </div>
  );
}
