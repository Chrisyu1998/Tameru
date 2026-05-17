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
import { FIXTURE_CARDS, type Card, type CardProgram, type CardMultiplier, type Transaction } from "./fixtures";
import {
  deleteCard as apiDeleteCard,
  listCards as apiListCards,
  patchCard as apiPatchCard,
  type CardProgram as WireCardProgram,
  type CardRow,
} from "./cardsApi";
import {
  deleteTransaction as apiDeleteTransaction,
  listTransactions,
  patchTransaction,
  sanitizeCardId,
  type PatchTransactionBody,
} from "./transactionsApi";
import { useAppStore } from "../store";

export interface PendingDeleteState {
  id: string;
  /** Wall-clock ms when the timer started, for rAF-driven progress UIs. */
  scheduledAt: number;
  /** Total grace window in ms. */
  durationMs: number;
}

interface LedgerState {
  transactions: Transaction[];
  cards: Card[];
  /** True while the initial GET /transactions is in flight. */
  loading: boolean;
  /** True once we've fetched at least once for this signed-in session. */
  loaded: boolean;
  /**
   * Rows mid-deletion. Keyed by transaction id. The row stays in
   * `transactions` until the timer commits — so the list can render it
   * with a countdown progress bar.
   */
  pendingDeletes: Record<string, PendingDeleteState>;
  /**
   * Cards mid-deletion. Keyed by card id. The card stays in `cards`
   * until the timer commits, mirroring the transaction undo pattern so
   * the card row can render the same countdown line.
   */
  pendingCardDeletes: Record<string, PendingDeleteState>;
}

let state: LedgerState = {
  transactions: [],
  cards: FIXTURE_CARDS,
  loading: false,
  loaded: false,
  pendingDeletes: {},
  pendingCardDeletes: {},
};

/*
 * Timer handles live at module scope on purpose: page-local state was the
 * source of the "navigate away during undo window → delete never fires" bug.
 * The Map is not reactive; reactivity comes from `state.pendingDeletes`.
 */
const pendingTimers = new Map<string, ReturnType<typeof setTimeout>>();

// Day 14 — separate timer map for the card-delete undo window. Cards
// soft-delete on the server (DESIGN.md §8.1), but we delay the network
// call by the undo grace period so an "undo" can cancel before the
// server flips status='deleted'. Symmetric to the transaction pattern;
// reactivity comes from `state.pendingCardDeletes`.
const cardDeleteTimers = new Map<string, ReturnType<typeof setTimeout>>();

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

function setState(next: Partial<LedgerState>) {
  state = { ...state, ...next };
  emit();
}

/*
 * Auth-driven refresh: when a JWT appears AND the user is bootstrapped
 * (home_currency is set), fetch. When the JWT disappears (sign out,
 * displaced), drop the in-memory transactions so we don't render a previous
 * user's data on the next mount. Cards stay (they're fixtures).
 *
 * The home_currency gate matters: /transactions runs through the
 * single-active-device check in app/auth.py:113, which 401s with
 * DEVICE_DISPLACED when no users_meta row exists. A brand-new OAuth user
 * has a JWT before they have a users_meta row — firing /transactions there
 * pops the displacement modal on top of the onboarding currency picker.
 *
 * We subscribe at module load — the store is a singleton, so this fires
 * exactly once per page load no matter how many places import `ledger`.
 */
let _lastJwt: string | null = null;
useAppStore.subscribe((s) => {
  const bootstrapped = !!s.jwt && typeof s.homeCurrency === "string";
  if (bootstrapped && s.jwt !== _lastJwt) {
    _lastJwt = s.jwt;
    void ledger.refresh();
    void ledger.refreshCards();
  } else if (!s.jwt && _lastJwt) {
    _lastJwt = null;
    // Cancel any in-flight delete timers — they'd hit a 401 with no JWT,
    // and the row's already gone from the user's perspective anyway.
    for (const timer of pendingTimers.values()) clearTimeout(timer);
    pendingTimers.clear();
    for (const timer of cardDeleteTimers.values()) clearTimeout(timer);
    cardDeleteTimers.clear();
    setState({
      transactions: [],
      cards: FIXTURE_CARDS,
      loading: false,
      loaded: false,
      pendingDeletes: {},
      pendingCardDeletes: {},
    });
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
    return {
      transactions: [],
      cards: FIXTURE_CARDS,
      loading: false,
      loaded: false,
      pendingDeletes: {},
      pendingCardDeletes: {},
    };
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
   * Schedule a delete with an undo window. The row STAYS in `transactions`
   * for `durationMs` (so the list can render a countdown progress bar on
   * it); when the timer fires we remove it locally and call DELETE. Calling
   * `undoDelete` before then cancels the commit cleanly.
   *
   * Idempotent — a second `scheduleDelete` on an already-pending id is a
   * no-op (we don't restart the timer). Calling on an unknown id is also
   * a no-op.
   */
  scheduleDelete(id: string, durationMs: number = 5000): void {
    if (pendingTimers.has(id)) return;
    const row = state.transactions.find((t) => t.id === id);
    if (!row) return;
    const scheduledAt = Date.now();
    setState({
      pendingDeletes: {
        ...state.pendingDeletes,
        [id]: { id, scheduledAt, durationMs },
      },
    });
    const timer = setTimeout(() => {
      pendingTimers.delete(id);
      // Snapshot original index so we can restore on server failure.
      const idx = state.transactions.findIndex((t) => t.id === id);
      const { [id]: _, ...restPending } = state.pendingDeletes;
      setState({
        transactions: state.transactions.filter((t) => t.id !== id),
        pendingDeletes: restPending,
      });
      void apiDeleteTransaction(id).catch((err) => {
        // Server-side delete failed (network, RLS race, etc.). Put the row
        // back so the user isn't silently lying-to about state. Original
        // index used when possible.
        const next = [...state.transactions];
        next.splice(Math.max(0, Math.min(idx, next.length)), 0, row);
        setState({ transactions: next });
        // eslint-disable-next-line no-console
        console.warn("ledger commit-delete failed; restored", err);
      });
    }, durationMs);
    pendingTimers.set(id, timer);
  },

  /**
   * Cancel a pending delete. Safe to call on an unknown id.
   */
  undoDelete(id: string): void {
    const timer = pendingTimers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      pendingTimers.delete(id);
    }
    if (state.pendingDeletes[id] !== undefined) {
      const { [id]: _, ...rest } = state.pendingDeletes;
      setState({ pendingDeletes: rest });
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

  /* ─── Cards: wired to /cards backend as of Day 14 ────────────── */

  /**
   * Fetch /cards and replace local state.
   *
   * Called automatically on JWT change (alongside `refresh()`). Maps the
   * `CardRow` wire shape to the local `Card` shape consumed by every
   * Lovable-imported card-rendering component. The mapping intentionally
   * drops `source_urls`, `deleted_at`, and `program` strings outside
   * the Lovable enum (folded to "Cash") — those aren't displayed on the
   * post-onboarding cards list, which is the only consumer of the
   * default `cards` view. The breakdown filter calls
   * `refreshCardsIncludingInactive()` if it needs deleted rows.
   */
  async refreshCards(): Promise<void> {
    const { jwt } = useAppStore.getState();
    if (!jwt) return;
    try {
      const resp = await apiListCards();
      setState({ cards: resp.items.map(cardRowToFixture) });
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("ledger cards refresh failed", err);
    }
  },

  /**
   * Local optimistic add for a newly-confirmed card. Called from the
   * chat commit path and the offline-drain success hook so the cards
   * page reflects the new row without waiting for the next /cards
   * fetch (which today only fires on JWT change at startup). Mirrors
   * `addTransaction`. Idempotent on row id — a same-id re-add (e.g.
   * crid-replay returning the existing row) replaces in place rather
   * than duplicating, which matters for the drain path where the same
   * row can arrive twice across a reconnect.
   */
  addCard(row: CardRow): Card {
    const card = cardRowToFixture(row);
    const existingIdx = state.cards.findIndex((c) => c.id === card.id);
    if (existingIdx >= 0) {
      const next = [...state.cards];
      next[existingIdx] = card;
      setState({ cards: next });
    } else {
      setState({ cards: [...state.cards, card] });
    }
    return card;
  },

  /**
   * PATCH /cards/{id} with the supplied delta. Optimistically updates
   * local state with the patch; on success swaps in the server's
   * canonical row; on failure reverts. Mirrors `updateTransaction`.
   *
   * Mutable fields per the backend `CardPatchRequest`: name, program,
   * multipliers, annual_fee, color. Identity fields (issuer, network,
   * last_four) are not patchable on the server — supply them here and
   * the server simply ignores them.
   */
  async updateCard(
    id: string,
    patch: Partial<{
      name: string;
      program: CardProgram | undefined;
      multipliers: CardMultiplier[];
      annualFee: string | null;
      color: string | null;
    }>,
  ): Promise<void> {
    const prior = state.cards.find((c) => c.id === id);
    if (!prior) return;
    // null on the wire means "clear it" — fold to undefined on the
    // local Card shape so the optimistic copy stays in-type.
    const optimistic: Card = {
      ...prior,
      ...(patch.name !== undefined && { name: patch.name }),
      ...(patch.program !== undefined && { program: patch.program }),
      ...(patch.multipliers !== undefined && { multipliers: patch.multipliers }),
      ...(patch.annualFee !== undefined && { annualFee: patch.annualFee }),
      ...(patch.color !== undefined && { color: patch.color ?? undefined }),
    };
    setState({
      cards: state.cards.map((c) => (c.id === id ? optimistic : c)),
    });
    const body: Parameters<typeof apiPatchCard>[1] = {};
    if (patch.name !== undefined) body.name = patch.name;
    if (patch.program !== undefined) {
      body.program = FIXTURE_PROGRAM_TO_WIRE[patch.program ?? "Cash"];
    }
    if (patch.multipliers !== undefined) {
      body.multipliers = Object.fromEntries(
        patch.multipliers.map((m) => [m.label, m.factor]),
      );
    }
    if (patch.annualFee !== undefined) body.annual_fee = patch.annualFee;
    if (patch.color !== undefined) body.color = patch.color;
    try {
      const updated = await apiPatchCard(id, body);
      setState({
        cards: state.cards.map((c) => (c.id === id ? cardRowToFixture(updated) : c)),
      });
    } catch (err) {
      setState({
        cards: state.cards.map((c) => (c.id === id ? prior : c)),
      });
      // eslint-disable-next-line no-console
      console.warn("ledger card update failed; reverted", err);
    }
  },

  /**
   * Schedule a card delete with an undo window. The card STAYS in
   * `cards` for `durationMs` (so the list can render a countdown
   * progress line on it); when the timer fires we remove it locally
   * and call DELETE. Calling `undoDeleteCard` before then cancels the
   * commit cleanly.
   *
   * Symmetric to `scheduleDelete` for transactions. Idempotent — a
   * second call on an already-pending id is a no-op. Soft-delete on
   * the server flips `status='deleted'` + stamps `deleted_at`
   * (DESIGN.md §8.1); deleted rows are never revived — a re-add via
   * /cards/confirm produces a fresh `card_id`.
   */
  scheduleDeleteCard(id: string, durationMs: number = 5000): void {
    if (cardDeleteTimers.has(id)) return;
    const card = state.cards.find((c) => c.id === id);
    if (!card) return;
    const scheduledAt = Date.now();
    setState({
      pendingCardDeletes: {
        ...state.pendingCardDeletes,
        [id]: { id, scheduledAt, durationMs },
      },
    });
    const timer = setTimeout(() => {
      cardDeleteTimers.delete(id);
      const idx = state.cards.findIndex((c) => c.id === id);
      const { [id]: _, ...restPending } = state.pendingCardDeletes;
      setState({
        cards: state.cards.filter((c) => c.id !== id),
        pendingCardDeletes: restPending,
      });
      void apiDeleteCard(id).catch((err) => {
        const next = [...state.cards];
        next.splice(Math.max(0, Math.min(idx, next.length)), 0, card);
        setState({ cards: next });
        // eslint-disable-next-line no-console
        console.warn("ledger card commit-delete failed; restored", err);
      });
    }, durationMs);
    cardDeleteTimers.set(id, timer);
  },

  /**
   * Cancel a pending card delete. Safe to call on an unknown id.
   */
  undoDeleteCard(id: string): void {
    const timer = cardDeleteTimers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      cardDeleteTimers.delete(id);
    }
    if (state.pendingCardDeletes[id] !== undefined) {
      const { [id]: _, ...rest } = state.pendingCardDeletes;
      setState({ pendingCardDeletes: rest });
    }
  },

  /* ─── Bulk ops used by the sidebar's dev shortcuts ───────────── */

  setTransactions(txs: Transaction[]) {
    setState({ transactions: txs });
  },

  /**
   * Sidebar shortcut — clears the LOCAL view only. The server-side rows
   * remain; the next refresh will pull them back. Kept for the dev-only
   * "clear ledger" sidebar button; not a feature.
   */
  clear() {
    setState({ transactions: [] });
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

/* ─── CardRow → Card mapper (backend wire shape → Lovable shape) ─── */

// Coarse mapping from the backend's CardProgram enum onto the existing
// Lovable Card type. The Lovable type predates Day 14's enum and uses
// "ThankYou" / "Cash" labels; anything not directly representable folds
// to "Cash" (visually a neutral chip).
const PROGRAM_TO_FIXTURE: Record<string, CardProgram | undefined> = {
  UR: "UR",
  MR: "MR",
  Bilt: "Bilt",
  TYP: "ThankYou",
  Other: "Cash",
};

// Inverse of PROGRAM_TO_FIXTURE for round-tripping an edit patch back
// onto the wire. Lovable's "ThankYou" maps to the backend's "TYP";
// "Cash" folds to the backend's "Other".
const FIXTURE_PROGRAM_TO_WIRE: Record<CardProgram, WireCardProgram> = {
  UR: "UR",
  MR: "MR",
  Bilt: "Bilt",
  ThankYou: "TYP",
  Cash: "Other",
};

export function cardRowToFixture(row: CardRow): Card {
  const program = PROGRAM_TO_FIXTURE[row.program];
  const multipliers: CardMultiplier[] = Object.entries(row.multipliers ?? {})
    .map(([label, factor]) => ({ label, factor: Number(factor) }))
    .filter((m) => Number.isFinite(m.factor) && m.factor > 0)
    // Highest multiplier first so the most valuable bonus reads first.
    .sort((a, b) => b.factor - a.factor);
  return {
    id: row.id,
    name: row.name,
    last4: row.last_four ?? "",
    color: row.color ?? undefined,
    program,
    issuer: row.issuer,
    multipliers: multipliers.length > 0 ? multipliers : undefined,
    annualFee: row.annual_fee,
  };
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
