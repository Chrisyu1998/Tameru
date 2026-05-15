/**
 * Ledger store — `transactions` from the real backend, `cards` still local.
 *
 * The hook signature (`useLedger() => { transactions, cards }`) is preserved
 * so the dozens of consumers Lovable shipped don't need to change. What did
 * change:
 *   - Transactions are fetched from GET /transactions on the first subscribe
 *     after sign-in. Subsequent mutations call PATCH/DELETE and update local
 *     state optimistically; on failure they revert and surface in console.
 *   - Cards remain on FIXTURE_CARDS — there's no /cards endpoint in v1, so
 *     the Cards page is still demo data. Replace this when DESIGN.md §6 ships
 *     a /cards path.
 *
 * Subscription model is unchanged (useSyncExternalStore + listener Set).
 * Auth state from the store decides when to fetch — we re-fetch when a JWT
 * becomes available and clear the cache when it goes away.
 */

import { useSyncExternalStore } from "react";
import { FIXTURE_CARDS, type Card, type Transaction } from "./fixtures";
import {
  deleteTransaction as apiDeleteTransaction,
  listTransactions,
  patchTransaction,
  sanitizeCardId,
  type PatchTransactionBody,
} from "./transactionsApi";
import { useAppStore } from "../store";

interface LedgerState {
  transactions: Transaction[];
  cards: Card[];
  /** True while the initial GET /transactions is in flight. */
  loading: boolean;
  /** True once we've fetched at least once for this signed-in session. */
  loaded: boolean;
}

let state: LedgerState = {
  transactions: [],
  cards: FIXTURE_CARDS,
  loading: false,
  loaded: false,
};

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

function setState(next: Partial<LedgerState>) {
  state = { ...state, ...next };
  emit();
}

/*
 * Auth-driven refresh: when a JWT appears, fetch. When it disappears (sign
 * out, displaced), drop the in-memory transactions so we don't render a
 * previous user's data on the next mount. Cards stay (they're fixtures).
 *
 * We subscribe at module load — the store is a singleton, so this fires
 * exactly once per page load no matter how many places import `ledger`.
 */
let _lastJwt: string | null = null;
useAppStore.subscribe((s) => {
  if (s.jwt && s.jwt !== _lastJwt) {
    _lastJwt = s.jwt;
    void ledger.refresh();
  } else if (!s.jwt && _lastJwt) {
    _lastJwt = null;
    setState({ transactions: [], loading: false, loaded: false });
  }
});

export const ledger = {
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
  getSnapshot(): LedgerState {
    return state;
  },
  getServerSnapshot(): LedgerState {
    // SSR / first-paint — no transactions, no loading flag. The Vite SPA
    // doesn't actually SSR; this is here so useSyncExternalStore is happy.
    return { transactions: [], cards: FIXTURE_CARDS, loading: false, loaded: false };
  },

  /**
   * Fetch /transactions and replace local state. Called automatically when
   * a JWT lands in the store; callers that need a manual refresh (e.g. a
   * pull-to-refresh, or after an external write) can call this directly.
   */
  async refresh(): Promise<void> {
    const { jwt } = useAppStore.getState();
    if (!jwt) return;
    setState({ loading: true });
    try {
      const txs = await listTransactions();
      setState({ transactions: txs, loading: false, loaded: true });
    } catch (err) {
      // Don't clobber the existing list on a transient fetch failure —
      // the displaced modal handler already catches the auth error
      // class, and a refetch will happen on the next sign-in or chat
      // commit.
      // eslint-disable-next-line no-console
      console.warn("ledger refresh failed", err);
      setState({ loading: false });
    }
  },

  /**
   * Local optimistic add. Used by the chat-confirm flow — the server has
   * already returned the row, we just splice it into local state so the
   * dashboard reflects it without a separate refetch. New rows go to the
   * front (most recent first) which matches the GET order.
   */
  addTransaction(tx: Transaction): Transaction {
    setState({ transactions: [tx, ...state.transactions] });
    return tx;
  },

  /**
   * PATCH /transactions/{id} with the supplied delta. Optimistically
   * updates local state with the patch; on success swaps in the server's
   * canonical row; on failure reverts.
   */
  async updateTransaction(id: string, patch: Partial<Transaction>): Promise<void> {
    const prior = state.transactions.find((t) => t.id === id);
    if (!prior) return;
    const optimistic: Transaction = { ...prior, ...patch };
    setState({
      transactions: state.transactions.map((t) => (t.id === id ? optimistic : t)),
    });
    const body: PatchTransactionBody = {};
    if (patch.merchant !== undefined) body.merchant = patch.merchant;
    if (patch.amountCents !== undefined) body.amount = (patch.amountCents / 100).toFixed(2);
    if (patch.date !== undefined) body.date = patch.date;
    if (patch.cardId !== undefined) body.card_id = sanitizeCardId(patch.cardId);
    if (patch.category !== undefined) body.category = patch.category;
    try {
      const updated = await patchTransaction(id, body);
      setState({
        transactions: state.transactions.map((t) => (t.id === id ? updated : t)),
      });
    } catch (err) {
      // Revert.
      setState({
        transactions: state.transactions.map((t) => (t.id === id ? prior : t)),
      });
      // eslint-disable-next-line no-console
      console.warn("ledger update failed; reverted", err);
    }
  },

  /**
   * DELETE /transactions/{id}. Optimistically removes from local state; on
   * failure restores at the prior index.
   */
  async deleteTransaction(id: string): Promise<void> {
    const idx = state.transactions.findIndex((t) => t.id === id);
    if (idx === -1) return;
    const removed = state.transactions[idx];
    setState({
      transactions: state.transactions.filter((t) => t.id !== id),
    });
    try {
      await apiDeleteTransaction(id);
    } catch (err) {
      const next = [...state.transactions];
      next.splice(idx, 0, removed);
      setState({ transactions: next });
      // eslint-disable-next-line no-console
      console.warn("ledger delete failed; restored", err);
    }
  },

  /* ─── Cards: local-only stubs (no backend in v1) ─────────────── */

  deleteCard(id: string) {
    setState({ cards: state.cards.filter((c) => c.id !== id) });
  },

  insertCard(card: Card, atIndex: number) {
    const next = [...state.cards];
    next.splice(Math.max(0, Math.min(atIndex, next.length)), 0, card);
    setState({ cards: next });
  },

  /* ─── Bulk ops used by the sidebar's dev shortcuts ───────────── */

  setTransactions(txs: Transaction[]) {
    setState({ transactions: txs });
  },

  /**
   * Sidebar shortcut — clears the LOCAL view only. The server-side rows
   * remain; the next refresh will pull them back. Kept for the dev-only
   * "clear ledger" / "restore sample data" sidebar buttons; not a feature.
   */
  clear() {
    setState({ transactions: [] });
  },

  resetToFixtures() {
    // No-op in the real-data world — calling this would put demo rows
    // alongside live ones in the UI and confuse the user. We log instead
    // so the sidebar button's click is visible during development.
    // eslint-disable-next-line no-console
    console.warn("ledger.resetToFixtures is a no-op when wired to backend");
  },
};

export function useLedger(): LedgerState {
  return useSyncExternalStore(ledger.subscribe, ledger.getSnapshot, ledger.getServerSnapshot);
}

/* ────────────────────────────────────────────────────────────────────
 * Selectors / pure helpers (unchanged from Lovable)
 * ──────────────────────────────────────────────────────────────────── */

export function isCurrentMonth(isoDate: string, ref = new Date()): boolean {
  const d = new Date(isoDate + "T00:00:00");
  return (
    d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth()
  );
}

export function currentMonthTransactions(transactions: Transaction[]): Transaction[] {
  return transactions.filter((t) => isCurrentMonth(t.date));
}

export function totalCents(transactions: Transaction[]): number {
  return transactions.reduce((s, t) => s + t.amountCents, 0);
}

/* ─── First-transaction caption flag (still localStorage) ───────── */

const HINT_KEY = "tameru-first-hint-dismissed";

export function isFirstHintDismissed(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return window.localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return true;
  }
}

export function dismissFirstHint() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(HINT_KEY, "1");
  } catch {
    // ignore
  }
}
