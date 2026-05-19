/**
 * PostHog ships on Day 26. Until then, track() is a no-op stub so the rest
 * of the app can wire emit-sites today (voice errors, etc.) and have them
 * light up automatically when Day 26 replaces this module.
 *
 * The signature mirrors the Day-26 contract: a structural event name plus
 * a flat property bag. No financial data, no question text — see
 * CLAUDE.md (PostHog posture).
 */
export function track(eventName: string, properties: Record<string, unknown> = {}): void {
  void eventName;
  void properties;
}
