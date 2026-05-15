/** Mock subscriptions store (in-memory, reset per session). */

import { useSyncExternalStore } from "react";
import type { Category } from "./categories";

export type Frequency = "monthly" | "yearly" | "weekly";
export type SubStatus = "active" | "paused";

export interface Subscription {
  id: string;
  name: string;
  amountCents: number;
  frequency: Frequency;
  /** ISO date YYYY-MM-DD of next billing (for active) or last billing (for paused). */
  nextBilling: string;
  cardId: string;
  category: Category;
  /** ISO date the subscription started. */
  startedOn: string;
  status: SubStatus;
  /** True when tameru detected this from the ledger rather than the user adding it. */
  autoLogged?: boolean;
}

function isoFromNow(daysOffset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + daysOffset);
  return d.toISOString().slice(0, 10);
}

function isoYearsAgo(years: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

const SEED: Subscription[] = [
  {
    id: "sub-spotify",
    name: "Spotify",
    amountCents: 1199,
    frequency: "monthly",
    nextBilling: isoFromNow(8),
    cardId: "card-citi",
    category: "Subscriptions",
    startedOn: isoYearsAgo(3),
    status: "active",
    autoLogged: true,
  },
  {
    id: "sub-nyt",
    name: "NYT",
    amountCents: 1700,
    frequency: "monthly",
    nextBilling: isoFromNow(15),
    cardId: "card-citi",
    category: "Subscriptions",
    startedOn: isoYearsAgo(2),
    status: "active",
    autoLogged: true,
  },
  {
    id: "sub-icloud",
    name: "iCloud+",
    amountCents: 299,
    frequency: "monthly",
    nextBilling: isoFromNow(2),
    cardId: "card-citi",
    category: "Subscriptions",
    startedOn: isoYearsAgo(4),
    status: "active",
  },
  {
    id: "sub-netflix",
    name: "Netflix",
    amountCents: 1599,
    frequency: "monthly",
    nextBilling: isoFromNow(11),
    cardId: "card-amex",
    category: "Entertainment",
    startedOn: isoYearsAgo(5),
    status: "active",
  },
  {
    id: "sub-nyt-cooking",
    name: "NYT Cooking",
    amountCents: 500,
    frequency: "monthly",
    nextBilling: isoFromNow(-32),
    cardId: "card-citi",
    category: "Subscriptions",
    startedOn: isoYearsAgo(1),
    status: "paused",
    autoLogged: true,
  },
  {
    id: "sub-headspace",
    name: "Headspace",
    amountCents: 6999,
    frequency: "yearly",
    nextBilling: isoFromNow(-60),
    cardId: "card-amex",
    category: "Health",
    startedOn: isoYearsAgo(2),
    status: "paused",
  },
];

let state: Subscription[] = SEED;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());
const setState = (next: Subscription[]) => {
  state = next;
  emit();
};

export const subscriptions = {
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  getSnapshot() {
    return state;
  },
  getServerSnapshot() {
    return SEED;
  },
  pause(id: string) {
    setState(state.map((s) => (s.id === id ? { ...s, status: "paused" } : s)));
  },
  resume(id: string) {
    setState(state.map((s) => (s.id === id ? { ...s, status: "active" } : s)));
  },
  cancel(id: string) {
    setState(state.filter((s) => s.id !== id));
  },
  reset() {
    setState(SEED);
  },
};

export function useSubscriptions(): Subscription[] {
  return useSyncExternalStore(
    subscriptions.subscribe,
    subscriptions.getSnapshot,
    subscriptions.getServerSnapshot
  );
}

export function formatFrequency(f: Frequency): string {
  return f === "monthly" ? "monthly" : f === "yearly" ? "yearly" : "weekly";
}
