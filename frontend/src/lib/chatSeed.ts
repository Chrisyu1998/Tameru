/**
 * Tiny session-scoped seed bus so other pages can pre-fill the next
 * /chat visit's input. Reset on browser tab close.
 */

const KEY = "tameru-chat-seed";

export function setChatSeed(text: string) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(KEY, text);
  } catch {
    // ignore
  }
}

export function consumeChatSeed(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.sessionStorage.getItem(KEY);
    if (v) window.sessionStorage.removeItem(KEY);
    return v;
  } catch {
    return null;
  }
}
