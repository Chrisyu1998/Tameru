/**
 * Subscription store — API-backed (Day 19).
 *
 * Replaces the previous in-memory mock. Subscriptions fetch from
 * `GET /subscriptions?status=all` on first subscribe (after sign-in) and
 * after explicit refreshes. Mutations call the typed API and update
 * local state optimistically; on failure they revert.
 *
 * `useSyncExternalStore` keeps the React surface similar to the prior
 * mock so callers don't have to change their hook usage; the row shape
 * changed from the mock's camelCase to the wire snake_case
 * (`SubscriptionRow` from `./subscriptionsApi`).
 */

import { useSyncExternalStore } from "react";
import {
  confirmSubscription,
  deleteSubscription as apiDeleteSubscription,
  listSubscriptions,
  patchSubscription,
  type Frequency,
  type SubscriptionProposal,
  type SubscriptionRow,
  type SubscriptionStatus,
} from "./subscriptionsApi";
import { useAppStore } from "../store";

interface SubscriptionsState {
  items: SubscriptionRow[];
  loading: boolean;
  loaded: boolean;
}

let state: SubscriptionsState = {
  items: [],
  loading: false,
  loaded: false,
};

const listeners = new Set<() => void>();
const emit = (): void => {
  for (const l of listeners) l();
};
const setState = (patch: Partial<SubscriptionsState>): void => {
  state = { ...state, ...patch };
  emit();
};

let _authSubscribed = false;
let _lastUserId: string | null = null;

function _ensureAuthHook(): void {
  if (_authSubscribed) return;
  _authSubscribed = true;
  // Track the actual user id so a direct user-A → user-B switch
  // (sign-out + sign-in inside the same tab, single-active-device
  // flow) clears the prior user's rows instead of silently leaving
  // them on screen. The previous `s.user ? "live" : null` collapsed
  // both signed-in users to the same string, so this subscriber
  // never fired on the swap and the page kept rendering A's
  // subscriptions to B until reload. Mirrors ledger.ts.
  useAppStore.subscribe((s) => {
    const userId = s.user?.id ?? null;
    if (userId === _lastUserId) return;
    _lastUserId = userId;
    // Always blow away the in-memory rows before refetching so a slow
    // network can't briefly show stale rows under the new user. The
    // refetch then populates with the correct user's data.
    setState({ items: [], loaded: false, loading: false });
    if (userId) {
      void refreshSubscriptions();
    }
  });
  const initial = useAppStore.getState().user?.id ?? null;
  if (initial) {
    _lastUserId = initial;
    void refreshSubscriptions();
  }
}

/**
 * Refetch all subscriptions for the signed-in user. Called by the auth
 * subscriber, by the `/subscriptions` page mount, and after the chat
 * drain success hook commits a new row.
 */
export async function refreshSubscriptions(): Promise<void> {
  if (!useAppStore.getState().user) return;
  setState({ loading: true });
  try {
    const resp = await listSubscriptions("all");
    setState({ items: resp.items, loading: false, loaded: true });
  } catch {
    setState({ loading: false });
  }
}

/**
 * Add a row to the in-memory list without a round-trip. Used by the
 * chat-store drain success hook and direct confirm flows. Deduped on
 * id so a refetch after the local add doesn't double-list.
 */
export function addSubscriptionLocal(row: SubscriptionRow): void {
  if (state.items.some((s) => s.id === row.id)) return;
  setState({ items: [...state.items, row] });
}

export async function pauseSubscription(id: string): Promise<void> {
  const prev = state.items;
  setState({
    items: prev.map((s) => (s.id === id ? { ...s, status: "paused" } : s)),
  });
  try {
    await patchSubscription(id, { status: "paused" });
  } catch {
    setState({ items: prev });
  }
}

export async function resumeSubscription(id: string): Promise<void> {
  const prev = state.items;
  setState({
    items: prev.map((s) => (s.id === id ? { ...s, status: "active" } : s)),
  });
  try {
    await patchSubscription(id, { status: "active" });
  } catch {
    setState({ items: prev });
  }
}

export async function cancelSubscription(id: string): Promise<void> {
  const prev = state.items;
  setState({
    items: prev.map((s) =>
      s.id === id ? { ...s, status: "cancelled" } : s,
    ),
  });
  try {
    await apiDeleteSubscription(id);
  } catch {
    setState({ items: prev });
  }
}

/**
 * General-purpose patch: any subset of `{name, amount, category, card_id}`.
 * Status changes still go through `pauseSubscription`/`resumeSubscription`/
 * `cancelSubscription` so the local optimistic update can scope to the
 * specific lifecycle field. Used by `EditSubscriptionSheet` (DESIGN.md
 * §6.5) so the user can edit field values without leaving the
 * subscriptions surface.
 *
 * Reverts the local row on failure so a 422 (e.g. `card_deleted` resume
 * guard) doesn't leave the UI showing values the server didn't accept.
 */
export async function updateSubscription(
  id: string,
  patch: {
    name?: string;
    amount?: string;
    category?: string;
    card_id?: string | null;
  },
): Promise<void> {
  const prev = state.items;
  setState({
    items: prev.map((s) => (s.id === id ? { ...s, ...patch } : s)),
  });
  try {
    const updated = await patchSubscription(id, patch);
    setState({
      items: state.items.map((s) => (s.id === id ? updated : s)),
    });
  } catch {
    setState({ items: prev });
  }
}

export async function reassignSubscriptionCard(
  id: string,
  cardId: string | null,
): Promise<void> {
  const prev = state.items;
  setState({
    items: prev.map((s) => (s.id === id ? { ...s, card_id: cardId } : s)),
  });
  try {
    await patchSubscription(id, { card_id: cardId });
  } catch {
    setState({ items: prev });
  }
}

/**
 * Commit a subscription proposal directly (used by the parse-card
 * "looks right" tap and by the offline-queue drain). On success the row
 * lands locally for immediate render; on failure the caller handles it.
 */
export async function commitSubscriptionProposal(
  proposal: SubscriptionProposal,
): Promise<SubscriptionRow> {
  const row = await confirmSubscription(proposal);
  addSubscriptionLocal(row);
  return row;
}

export function useSubscriptions(): SubscriptionsState {
  _ensureAuthHook();
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    () => state,
    () => state,
  );
}

/** Human-readable frequency label. Matches the wire enum verbatim. */
export function formatFrequency(f: Frequency): string {
  return f;
}

export type {
  SubscriptionRow,
  SubscriptionProposal,
  SubscriptionStatus,
  Frequency,
};
