/** First-run flag for onboarding. */

const KEY = "tameru-onboarded";

export function hasOnboarded(): boolean {
  if (typeof window === "undefined") return true; // SSR: don't redirect
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return true;
  }
}

export function markOnboarded() {
  try {
    window.localStorage.setItem(KEY, "1");
  } catch {
    // ignore
  }
}

export function resetOnboarded() {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
