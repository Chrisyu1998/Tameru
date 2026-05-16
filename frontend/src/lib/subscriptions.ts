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

const SEED: Subscription[] = [];

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
