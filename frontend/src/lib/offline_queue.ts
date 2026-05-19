/**
 * Offline confirm queue (Day 15).
 *
 * Scope: catches the narrow window between *parse-card-render* (online)
 * and *confirm-tap* (offline). The user types in chat → Claude returns a
 * proposal → the UI renders the parse card. If the user taps "looks
 * right" while offline, the confirm payload — including any edits to
 * amount/merchant/etc. captured by the ParseCard's local state — lands
 * here instead of dropping on the floor. On reconnect (or app mount when
 * online), the drain replays each entry in FIFO order to its confirm
 * endpoint, then patches the matching in-memory parse card to its
 * `logged.` / `added.` state without waiting for a page reload.
 *
 * Composition (typing a new transaction from scratch) is NOT supported
 * offline. The parse step runs server-side in the Claude agent loop, so
 * the user must be online when the proposal first appears. DESIGN.md
 * §10.1.
 *
 * Drain is window-scope, never service-worker. The SW can't read the
 * in-memory Supabase session token, and iOS Safari Background Sync isn't
 * reliable enough to depend on (DESIGN.md §10.2). Tameru's single-active-
 * device model (CLAUDE.md invariant 5) makes the foreground tab the
 * right scope anyway.
 *
 * Cross-user safety: each entry stores `ownerUserId` (Supabase user
 * UUID — not a credential; auth at drain time still flows from the in-
 * flight Supabase session). The drain only replays entries whose
 * `ownerUserId` matches the currently signed-in user. A sign-out → sign-
 * in-as-different-user flow leaves user A's queued entries dormant
 * until A signs back in.
 *
 * Idempotency: transaction payloads carry `client_request_id` (Day 5
 * server-side key); the drain MUST send the same id and MUST NOT
 * regenerate it. Cards have no `client_request_id`, but the partial
 * unique index `cards_active_identity_uniq` makes a duplicate confirm
 * a 409 `active_card_exists` that the drain treats as a successful
 * dequeue (the card is already in the wallet).
 */

import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import { ApiError } from "./api";
import { useAppStore } from "../store";
import {
  confirmTransaction,
  type ConfirmTransactionBody,
  type ConfirmTransactionResult,
} from "./transactionsApi";
import {
  confirmCard,
  isActiveCardExistsError,
  type CardProposal,
  type CardRow,
} from "./cardsApi";
import {
  confirmSubscription,
  type SubscriptionProposal,
  type SubscriptionRow,
} from "./subscriptionsApi";

const DB_NAME = "tameru-offline-queue";
const DB_VERSION = 1;
const STORE = "pending_confirms" as const;

/**
 * IndexedDB schema. `id` is the keyPath; `queued_at` is indexed so a
 * future migration could cursor in queued-order without an in-memory
 * sort. Today we sort in JS because the queue is small (≤ a handful per
 * session) and the index buys us nothing yet.
 */
interface OfflineQueueSchema extends DBSchema {
  pending_confirms: {
    key: string;
    value: PersistedQueueEntry;
    indexes: { "by-queued-at": string };
  };
}

/**
 * Wire shape stored in IndexedDB. `kind` discriminates the payload union.
 * Day 19 adds the `subscription` branch alongside transactions and cards;
 * the schema upgrade is additive (existing entries keep working) and no
 * IDB migration is needed because IndexedDB is schemaless at the
 * application level.
 */
export type PersistedQueueEntry =
  | TxQueueEntry
  | CardQueueEntry
  | SubscriptionQueueEntry;

export interface TxQueueEntry {
  id: string;
  ownerUserId: string;
  kind: "transaction";
  payload: ConfirmTransactionBody;
  /**
   * In-memory chat message id captured at enqueue time. The drain uses
   * it to find the right parse card to flip to `logged.` on success.
   * Transactions also fall back to `payload.client_request_id` so the
   * in-memory patch works across a same-session message-list rehydrate
   * (where the id changes but the crid doesn't).
   */
  messageId?: string;
  queuedAt: string; // ISO8601
}

export interface CardQueueEntry {
  id: string;
  ownerUserId: string;
  kind: "card";
  payload: CardProposal;
  /** See TxQueueEntry.messageId; cards have no crid fallback. */
  messageId?: string;
  queuedAt: string;
}

export interface SubscriptionQueueEntry {
  id: string;
  ownerUserId: string;
  kind: "subscription";
  payload: SubscriptionProposal;
  /**
   * See TxQueueEntry.messageId. Subscriptions carry `client_request_id`
   * on the payload (like transactions), so the in-memory patch can use
   * either the messageId or the crid to locate the right parse card.
   */
  messageId?: string;
  queuedAt: string;
}

/**
 * Outcome reported by the drain handler hooks (see `_drainOne`). Used
 * internally to decide whether to keep iterating or pause until the
 * next online event.
 */
type DrainOutcome = "completed" | "leave-in-queue";

/**
 * Shape the drain expects the chatStore to expose. Defined here as a
 * structural interface so the queue module doesn't statically import
 * chatStore (which itself imports this module — would be a cycle).
 * Dynamic import inside `_drainOne` is the actual binding.
 */
export interface DrainHooks {
  applyDrainTxSuccess(
    match: { clientRequestId: string; messageId?: string },
    result: ConfirmTransactionResult,
  ): void;
  applyDrainCardSuccess(
    match: { clientRequestId?: string; messageId?: string },
    card: CardRow,
  ): void;
  applyDrainCardConflict(
    match: { clientRequestId?: string; messageId?: string },
    detail: {
      existing_card_id: string;
      existing_card_name: string;
      existing_card_last_four: string | null;
    },
  ): void;
  /**
   * Day 19 — subscription confirm drained successfully. Same shape as
   * the card-success hook: clientRequestId/messageId locate the parse
   * card; the server-returned row is the source of truth for the
   * committed state. No analog of the card 409 path because
   * subscriptions dedup via `client_request_id` (a replay returns the
   * existing row with 2xx, not a 409).
   */
  applyDrainSubscriptionSuccess(
    match: { clientRequestId: string; messageId?: string },
    subscription: SubscriptionRow,
  ): void;
  applyDrainPermanentFailure(
    entry: PersistedQueueEntry,
    err: ApiError,
  ): void;
}

let _db: Promise<IDBPDatabase<OfflineQueueSchema>> | null = null;

/**
 * Open (lazily) the offline-queue IDB. Memoized — `openDB` is itself
 * idempotent but the wrapping promise lets concurrent callers share a
 * single open handshake.
 */
function db(): Promise<IDBPDatabase<OfflineQueueSchema>> {
  if (!_db) {
    _db = openDB<OfflineQueueSchema>(DB_NAME, DB_VERSION, {
      upgrade(database) {
        const store = database.createObjectStore(STORE, { keyPath: "id" });
        store.createIndex("by-queued-at", "queuedAt");
      },
    });
  }
  return _db;
}

/* ─── Pub/sub for the pending-sync banner ──────────────────────────── */

type Listener = () => void;
const listeners = new Set<Listener>();
let cachedCount = 0;

function notify(): void {
  for (const l of listeners) l();
}

/** Subscribe to pending-count changes. Returns an unsubscribe. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Snapshot of the pending count for the currently signed-in user. */
export function getPendingCount(): number {
  return cachedCount;
}

async function refreshCount(ownerUserId: string | null): Promise<void> {
  if (!ownerUserId) {
    cachedCount = 0;
    notify();
    return;
  }
  const entries = await listForUser(ownerUserId);
  cachedCount = entries.length;
  notify();
}

/* ─── CRUD ──────────────────────────────────────────────────────────── */

/**
 * Enqueue a confirm payload. Returns the persisted entry so callers can
 * stash `id` if they need to dequeue programmatically (rare — drain
 * owns dequeuing).
 *
 * Generates `id` via `crypto.randomUUID()` so concurrent enqueues from
 * the same session don't collide.
 */
export async function enqueue(
  entry:
    | Omit<TxQueueEntry, "id" | "queuedAt">
    | Omit<CardQueueEntry, "id" | "queuedAt">
    | Omit<SubscriptionQueueEntry, "id" | "queuedAt">,
): Promise<PersistedQueueEntry> {
  const full: PersistedQueueEntry = {
    ...entry,
    id: crypto.randomUUID(),
    queuedAt: new Date().toISOString(),
  } as PersistedQueueEntry;
  const database = await db();
  await database.put(STORE, full);
  await refreshCount(full.ownerUserId);
  return full;
}

/**
 * List entries for one user, sorted FIFO. The cross-user filter is the
 * load-bearing rule that prevents a sign-out → sign-in-as-different-user
 * flow from POSTing user A's queued confirm under user B's session.
 */
export async function listForUser(
  ownerUserId: string,
): Promise<PersistedQueueEntry[]> {
  const database = await db();
  const all = await database.getAll(STORE);
  return all
    .filter((e) => e.ownerUserId === ownerUserId)
    .sort((a, b) => a.queuedAt.localeCompare(b.queuedAt));
}

/** Remove one entry by id. Used by the drain after a terminal outcome. */
export async function deleteEntry(id: string): Promise<void> {
  const database = await db();
  await database.delete(STORE, id);
}

/** Wipe the entire store. Test-only — never call this from app code. */
export async function _clearAll(): Promise<void> {
  const database = await db();
  await database.clear(STORE);
  cachedCount = 0;
  notify();
}

/* ─── Drain ─────────────────────────────────────────────────────────── */

let _draining = false;

/**
 * Drain queued confirms for the currently signed-in user, FIFO.
 *
 * Silent no-ops when:
 *   - already draining (guard against re-entrant calls from a flurry of
 *     `online` events).
 *   - `navigator.onLine === false` (avoids a confusing 0-status error
 *     path; the next `online` event will retry).
 *   - no signed-in user (cross-user safety).
 *
 * Iterates FIFO. On a leave-in-queue outcome (network / 5xx), pauses
 * the loop — leaving later entries in place — so a transient server
 * failure doesn't burn through the queue. The next `online` event or
 * the next app mount will resume.
 *
 * Re-checks the user mid-loop: a sign-out partway through aborts cleanly
 * rather than POSTing the next entry under a stale session token. The
 * already-completed entries stay dequeued.
 */
export async function drainQueue(): Promise<void> {
  if (_draining) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) return;
  const user = useAppStore.getState().user;
  if (!user) return;
  _draining = true;
  try {
    const entries = await listForUser(user.id);
    for (const entry of entries) {
      const liveUser = useAppStore.getState().user;
      if (!liveUser || liveUser.id !== entry.ownerUserId) break;
      const outcome = await _drainOne(entry);
      if (outcome === "leave-in-queue") break;
    }
    await refreshCount(user.id);
  } finally {
    _draining = false;
  }
}

/**
 * Replay one entry. Outcome:
 *
 *   - **2xx** → delete from queue, call the chatStore hook to flip the
 *     matching in-memory parse card to its committed state using the
 *     response payload (NOT the queued body — the server's normalized
 *     view wins).
 *   - **409 `active_card_exists`** (cards only) → delete from queue,
 *     flip the in-memory card to committed using the existing row's id.
 *     The user already has this card; replaying a second time would 409
 *     again.
 *   - **other 4xx** → delete from queue, call the permanent-failure
 *     hook so the chatStore can re-render the parse card with a "this
 *     couldn't sync" affordance. Silent drop is forbidden — the user is
 *     online now and can fix or discard.
 *   - **5xx / network** → leave in queue for the next online event or
 *     app mount.
 */
async function _drainOne(
  entry: PersistedQueueEntry,
): Promise<DrainOutcome> {
  const { chatStore } = await import("./chatStore");
  try {
    if (entry.kind === "transaction") {
      const result = await confirmTransaction(entry.payload);
      chatStore.applyDrainTxSuccess(
        {
          clientRequestId: entry.payload.client_request_id,
          messageId: entry.messageId,
        },
        result,
      );
      await deleteEntry(entry.id);
      return "completed";
    }
    if (entry.kind === "subscription") {
      const subscription = await confirmSubscription(entry.payload);
      chatStore.applyDrainSubscriptionSuccess(
        {
          clientRequestId: entry.payload.client_request_id,
          messageId: entry.messageId,
        },
        subscription,
      );
      await deleteEntry(entry.id);
      return "completed";
    }
    const card = await confirmCard(entry.payload);
    chatStore.applyDrainCardSuccess(
      {
        clientRequestId: entry.payload.client_request_id,
        messageId: entry.messageId,
      },
      card,
    );
    await deleteEntry(entry.id);
    return "completed";
  } catch (err) {
    if (err instanceof ApiError) {
      if (entry.kind === "card" && isActiveCardExistsError(err)) {
        // The card is already in the wallet from a prior drain attempt
        // whose response was lost (or a separate session committed it).
        // Silent dequeue — flip the in-memory card to committed using
        // the existing row's id; the drain has no transient state of
        // its own to display, so we don't surface an error.
        chatStore.applyDrainCardConflict(
          {
            clientRequestId: entry.payload.client_request_id,
            messageId: entry.messageId,
          },
          err.body.detail,
        );
        await deleteEntry(entry.id);
        return "completed";
      }
      if (err.status >= 400 && err.status < 500) {
        // Permanent (per spec — 422 etc.). Drop from queue and let the
        // chatStore re-surface the proposal as a fixable parse card.
        chatStore.applyDrainPermanentFailure(entry, err);
        await deleteEntry(entry.id);
        return "completed";
      }
      // 5xx — leave for next online/mount.
      return "leave-in-queue";
    }
    // Non-ApiError thrown by fetch (TypeError, etc.) — network failure.
    return "leave-in-queue";
  }
}

/* ─── Auto-drain wiring ─────────────────────────────────────────────── */

let _autoDrainInstalled = false;

/**
 * Install the `online` listener and a mount-time drain. Idempotent —
 * safe to call from React effects that may re-fire. Returns a cleanup
 * fn (test-only; in app code the listener lives for the tab's lifetime).
 *
 * Also subscribes to the auth store so the banner count tracks the
 * currently signed-in user without each consumer wiring its own listener.
 */
export function setupAutoDrain(): () => void {
  if (_autoDrainInstalled) return () => undefined;
  _autoDrainInstalled = true;

  const onOnline = () => {
    void drainQueue();
  };
  if (typeof window !== "undefined") {
    window.addEventListener("online", onOnline);
  }

  if (typeof navigator !== "undefined" && navigator.onLine) {
    void drainQueue();
  }

  // Track auth changes so the badge count reflects the right user. Also
  // re-drain when a user signs in: their queued entries (queued under
  // their id earlier, then signed out) become eligible again.
  const unsub = useAppStore.subscribe((state) => {
    const id = state.user?.id ?? null;
    void refreshCount(id);
    if (id && typeof navigator !== "undefined" && navigator.onLine) {
      void drainQueue();
    }
  });

  // Seed the count for whoever is signed in at install time.
  void refreshCount(useAppStore.getState().user?.id ?? null);

  return () => {
    if (typeof window !== "undefined") {
      window.removeEventListener("online", onOnline);
    }
    unsub();
    _autoDrainInstalled = false;
  };
}

/**
 * Test-only reset. Closes the cached DB handle and clears in-process
 * state so each test starts from a clean slate. Production code should
 * never need this.
 */
export async function _resetForTests(): Promise<void> {
  if (_db) {
    const database = await _db;
    database.close();
  }
  _db = null;
  cachedCount = 0;
  _draining = false;
  _autoDrainInstalled = false;
  listeners.clear();
}
