import { useEffect, useState } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "tameru-theme";

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return "light";
}

function applyTheme(theme: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore storage errors
    }
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((t) => (t === "light" ? "dark" : "light")),
    setTheme,
  };
}
