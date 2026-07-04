/**
 * Shared chat session store. Used by both the mobile /chat route and the
 * desktop right-side ChatDrawer so they reflect a single conversation.
 *
 * Day 12: Talks to POST /chat/turn over Server-Sent Events. Tokens
 * stream into `streamingText` as they arrive; on the terminal `done`
 * frame we clear the streaming buffer and call _renderTurn to expand
 * the assembled tool_calls into ParseCard / CandidateList / Chart /
 * text bubbles (Day 10 contract — done.tool_calls is the exact Day 8
 * shape). On a terminal `error` frame or a network drop, any
 * accumulated text is committed as a partial bubble and `lastError`
 * is latched so the UI shows a Retry affordance. Re-firing re-uses
 * the original conversation_id; the backend writes nothing until
 * `done`, so retries are idempotent (DESIGN.md §7.5).
 *
 * `conversation_id` is minted server-side on the first turn and
 * replayed on every subsequent one so the agent loop sees the last
 * 5 turns of history (DESIGN.md §7.2.1).
 */

import { useEffect, useState } from "react";
import {
  isDailyCapEngaged,
  setDailyCapEngaged,
  newId,
  type CardParseDraft,
  type ChartSpec,
  type ChatMessage,
  type ParseDraft,
  type SubscriptionParseDraft,
  type ToolName,
} from "./chat";
import { ledger } from "./ledger";
import {
  confirmCard,
  isActiveCardExistsError,
  type CardIssuer,
  type CardNetwork,
  type CardProgram,
  type CardProposal,
  type CardRegion,
} from "./cardsApi";
import type { Category } from "./categories";
import type { Transaction } from "./fixtures";
import {
  getChatMessages,
  type ChatMessageWire,
  type ChatToolCall,
  type ChatTurnResponse,
} from "./chatApi";
import { streamTurn, type StreamError } from "./chat_stream";
import {
  confirmTransaction,
  fromWire,
  sanitizeCardId,
  type ConfirmTransactionBody,
  type ConfirmTransactionResult,
  type TransactionRowWire,
} from "./transactionsApi";
import { parseReceipt, type TransactionProposalWire } from "./receiptsApi";
import { ApiError } from "./api";
import { track } from "./analytics";
import { useAppStore } from "../store";
import type {
  ActiveCardExistsDetail,
  CardRow,
} from "./cardsApi";
import type { PersistedQueueEntry } from "./offline_queue";
import { addSubscriptionLocal } from "./subscriptions";
import {
  confirmSubscription,
  type Frequency as SubFrequency,
  type SubscriptionProposal,
  type SubscriptionRow,
} from "./subscriptionsApi";

type Listener = () => void;

/**
 * Latched after a terminal SSE error (or a network drop). Drives the
 * "Connection lost. [Retry]" affordance on the chat page. Cleared on a
 * successful send, on newChat(), or when retry() resolves.
 */
export interface ChatLastError {
  /** Friendly copy for the banner. Already-rendered, not a code. */
  message: string;
  /** The exact user message to re-fire when the user taps Retry. */
  pendingMessage: string;
}

/**
 * Per-page-load session metrics used by Day 26 analytics. Session is
 * defined as a single conversation: starts on the first successful turn
 * after `newChat()` (or app boot) and ends on the next `newChat()` /
 * sign-out. Lost on page reload (acceptable at v1 — cross-reload
 * sessions are rare and worth less than the persistence complexity).
 */
interface SessionMetrics {
  /** Conversation id of the active session, mirrored for the end event. */
  conversationId: string | null;
  /** Date.now() when the first turn of this session committed. */
  startedAt: number | null;
  /** Number of successful round-trips in this session. */
  turnCount: number;
}

interface ChatState {
  messages: ChatMessage[];
  /** Desktop drawer open. */
  drawerOpen: boolean;
  /** Drawer expanded to fill main pane. */
  drawerExpanded: boolean;
  /** Server-minted on first turn; sent back on every subsequent one. */
  conversationId: string | null;
  /** True while a turn is in flight. UI uses this to disable the input. */
  busy: boolean;
  /**
   * Accumulated assistant tokens during the active stream. The chat UI
   * renders this as the trailing bubble while busy; cleared on done /
   * error so _renderTurn / the error-recovery path own the final
   * rendering.
   */
  streamingText: string;
  /**
   * Latched when a stream terminates with `error` (non-cap) or a
   * network drop. UI swaps the daily-cap-or-input affordance for a
   * "Connection lost. [Retry]" banner. Null while the stream is healthy.
   */
  lastError: ChatLastError | null;
  /**
   * Latched when /chat/turn returns 429 UCAP_EXCEEDED. UI swaps the input
   * row for <DailyCapCard />. Cleared on newChat() or any subsequent
   * successful turn (e.g., after midnight UTC reset + new send).
   */
  capEngaged: boolean;
}

/**
 * localStorage key the chat page rehydrates from on mount. Persists the
 * server's conversation_id so a page refresh continues the same thread
 * instead of starting fresh (Day 10b §3). Cleared on `newChat()`.
 */
const CONVO_ID_KEY = "tameru-chat-conversation-id";

function readPersistedConvoId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(CONVO_ID_KEY);
  } catch {
    return null;
  }
}

function writePersistedConvoId(next: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (next === null) window.localStorage.removeItem(CONVO_ID_KEY);
    else window.localStorage.setItem(CONVO_ID_KEY, next);
  } catch {
    // ignore — localStorage can be unavailable in private mode
  }
}

let state: ChatState = {
  messages: [],
  drawerOpen: false,
  drawerExpanded: false,
  conversationId: readPersistedConvoId(),
  busy: false,
  streamingText: "",
  lastError: null,
  capEngaged: isDailyCapEngaged(),
};

/**
 * Module-scoped session metrics. Lives outside ChatState because no UI
 * reads it — only the Day 26 analytics events do. A page reload resets
 * it; that's fine (a "session" here is roughly "from new-chat to
 * new-chat within one tab"). When `conversationId` is non-null on app
 * boot but `startedAt` is null, the previous session's start is unknown
 * — we deliberately do NOT synthesize a fake start and we do NOT fire
 * `chat_session_started` for it; the next user-initiated newChat() will
 * close it out silently and a fresh send() will open a new measured
 * session.
 */
const sessionMetrics: SessionMetrics = {
  conversationId: null,
  startedAt: null,
  turnCount: 0,
};

function fireSessionEndIfActive(): void {
  if (
    sessionMetrics.conversationId === null ||
    sessionMetrics.startedAt === null ||
    sessionMetrics.turnCount === 0
  ) {
    sessionMetrics.conversationId = null;
    sessionMetrics.startedAt = null;
    sessionMetrics.turnCount = 0;
    return;
  }
  track("chat_session_ended", {
    conversation_id: sessionMetrics.conversationId,
    turn_count: sessionMetrics.turnCount,
    duration_ms: Date.now() - sessionMetrics.startedAt,
  });
  sessionMetrics.conversationId = null;
  sessionMetrics.startedAt = null;
  sessionMetrics.turnCount = 0;
}

// Day 26 (Codex 2026-05-23 P2): without this listener, `chat_session_ended`
// only fires when the user explicitly taps "new chat" — every other
// session-ending path (tab close, browser back to a different origin,
// PWA backgrounded on iOS) leaves a `chat_session_started` with no
// matching end event, breaking duration/turn-count analytics for the
// most common case. `pagehide` is the right lifecycle hook (more
// reliable than `beforeunload` on iOS per memory.md 2026-05-17), and
// posthog-js uses `navigator.sendBeacon` automatically during page
// unload so the queued event still ships. The `window` guard is for
// jsdom/test environments where `addEventListener` exists but the
// event never fires — registering it is harmless.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    fireSessionEndIfActive();
  });
}

const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l();
}

function setState(next: Partial<ChatState>) {
  state = { ...state, ...next };
  emit();
}

function appendMessages(...next: ChatMessage[]) {
  setState({ messages: [...state.messages, ...next] });
}

function appendAssistantText(text: string) {
  appendMessages({
    id: newId("ai"),
    role: "assistant",
    kind: "text",
    text,
  });
}

export const chatStore = {
  getSnapshot(): ChatState {
    return state;
  },
  subscribe(l: Listener) {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },

  /**
   * Send one turn over SSE. Appends the user bubble immediately, then
   * drives the SSE stream via `_streamOnce`. Re-entry is guarded by
   * `busy` — a second send before the first resolves is a no-op.
   */
  async send(raw: string) {
    const text = raw.trim();
    if (!text) return;
    if (state.busy) return;
    if (state.capEngaged) return;

    appendMessages({ id: newId("user"), role: "user", text });
    await _streamOnce(text);
  },

  /**
   * Scan a receipt photo into a transaction proposal. The image is already
   * downscaled + JPEG-re-encoded by the caller (chat.tsx); this uploads it to
   * `POST /receipts/parse` (one Gemini Vision call), appends the returned
   * proposal as a parse card, and lets the user confirm it through the same
   * `commitDraft` path as a chat-typed transaction (idempotency, entry-moment
   * insight, and the merchant-correction loop all inherited). The image is
   * never stored server-side. Re-entry is guarded by `busy` — and the camera
   * button is disabled while busy/offline, so this is belt-and-suspenders.
   */
  async sendReceiptPhoto(image: Blob) {
    if (state.busy) return;

    appendMessages({
      id: newId("user"),
      role: "user",
      text: "📷 receipt photo",
    });
    setState({ busy: true });
    try {
      const wire = await parseReceipt(image);
      const draft = _wireProposalToDraft(wire);
      if (!draft) {
        appendAssistantText(
          "couldn't read that receipt. try a clearer photo, or just type it.",
        );
        return;
      }
      appendMessages({
        id: newId("ai"),
        role: "assistant",
        kind: "parse",
        draft,
      });
    } catch (err) {
      appendAssistantText(_receiptErrorText(err));
    } finally {
      setState({ busy: false });
    }
  },

  /**
   * Re-fire the last failed message. Resolves silently if there's no
   * latched error. Does NOT re-append the user bubble — the original
   * is still in the thread from `send()`. The conversation_id is
   * unchanged by design: the backend wrote nothing on the failed
   * attempt (DESIGN.md §7.5), so the next turn sees the same prior
   * history and the retry is idempotent.
   */
  async retry() {
    const err = state.lastError;
    if (!err) return;
    if (state.busy) return;
    await _streamOnce(err.pendingMessage);
  },

  /** Manually clear the retry latch (user dismissed the banner). */
  dismissError() {
    setState({ lastError: null });
  },

  setMessages(messages: ChatMessage[]) {
    setState({ messages });
  },

  /**
   * Reset the visible thread and the server-side conversation pointer.
   * Next turn starts a fresh conversation (server mints a new UUID).
   * Also clears `lastError` so a stale "connection lost. retry?" banner
   * (whose pendingMessage referred to the now-discarded conversation)
   * doesn't outlive the conversation it belonged to.
   */
  newChat() {
    // Fire chat_session_ended *before* nulling state.conversationId so
    // the event carries the right id. No-op if no active session.
    fireSessionEndIfActive();
    writePersistedConvoId(null);
    setState({
      messages: [],
      conversationId: null,
      capEngaged: false,
      lastError: null,
      streamingText: "",
    });
  },

  /**
   * Explicit session-end hook for non-newChat session boundaries.
   * Called from auth.ts on sign-out / token-expiry so an in-flight
   * chat session doesn't leak past the session that owned it. The
   * `pagehide` listener above covers the tab-close path; this covers
   * the in-app sign-out path (Codex 2026-05-23 P2).
   *
   * Does NOT clear the rest of the chat state — that's `newChat()`'s
   * job. Sign-out's `clearSession()` doesn't touch chat thread state
   * either; the next sign-in starts fresh by rehydrating from the
   * server-side conversation_id.
   */
  endSession() {
    fireSessionEndIfActive();
  },

  /**
   * Pull /chat/messages for the current `conversationId` and replace the
   * thread. Called from the chat page mount when there's a persisted
   * conversation id but no in-memory messages.
   *
   * Map rules (Day 10b §3 spec — "for v1, all blocks render as text bubbles"):
   *   - user text blocks    → UserMessage
   *   - assistant text blocks → AssistantTextMessage (no `via` chip — we
   *     don't know which tool produced the text without the trace)
   *   - non-text blocks (tool_use/tool_result, future image)    → dropped
   *
   * Failures are swallowed deliberately — a refresh that can't reach the
   * backend should still render the empty composer rather than wedging
   * the UI on an error state.
   */
  async hydrateMessages(): Promise<void> {
    const cid = state.conversationId;
    if (!cid) return;
    if (state.messages.length > 0) return;
    try {
      const res = await getChatMessages(cid);
      const rehydrated = res.messages.flatMap((m) => _wireMessageToLocal(m));
      if (rehydrated.length > 0) {
        setState({ messages: rehydrated });
      }
    } catch {
      // see docstring — silent failure is intentional.
    }
  },

  /**
   * Commit a parse draft to the backend.
   *
   * Backend-sourced drafts carry `clientRequestId` (from the propose_transaction
   * tool result). We POST those to /transactions/confirm so they're idempotent
   * — replaying the same confirm returns the prior row instead of double-
   * inserting. After a successful commit we optimistically inject the new
   * row into the ledger so the dashboard reflects it without waiting for
   * a refetch.
   */
  async commitDraft(msgId: string, draft: ParseDraft | null) {
    if (!draft) return;

    if (!draft.clientRequestId) {
      // Defensive: a draft without a client_request_id can't be confirmed
      // server-side. This shouldn't happen with the wired chat — flag it
      // in the UI rather than silently dropping the click.
      appendAssistantText(
        "this draft is missing its proposal id — try asking again.",
      );
      return;
    }

    // The card picker is wired to Lovable's FIXTURE_CARDS (slug ids like
    // "card-amex") because there's no /cards endpoint yet. Sending those
    // verbatim 422s the Pydantic UUID validator on /transactions/confirm.
    // Until cards have a backend, anything that doesn't look like a UUID
    // gets dropped to null so the row commits cardless.
    const body: ConfirmTransactionBody = {
      merchant: draft.merchant,
      amount: (draft.amountCents / 100).toFixed(2),
      date: draft.date,
      card_id: sanitizeCardId(draft.cardId),
      category: draft.category,
      notes: draft.notes ?? null,
      gemini_suggestion: draft.geminiSuggestion ?? null,
      client_request_id: draft.clientRequestId,
      // Preserve where the draft came from so the committed row's `source` is
      // correct (`receipt_photo` for scanned receipts, `nlp` otherwise). The
      // server defaults to `nlp` if omitted.
      source: draft.source ?? "nlp",
    };

    // Pre-confirm snapshot: used to decide whether this confirm is the
    // user's first transaction (fires `firstTransaction` analytics
    // milestone). `loaded === false` means the ledger hasn't been pulled
    // yet — we can't know first-transaction status, so we skip the
    // milestone rather than risk a false positive.
    const ledgerPre = ledger.getSnapshot();
    const isFirstTransaction =
      ledgerPre.loaded && ledgerPre.transactions.length === 0;

    try {
      const result = await confirmTransaction(body);
      const tx = result.transaction;
      // Optimistic local injection. lib/ledger.ts also refetches on demand
      // via ledger.refresh(); calling it here would re-trip a network round
      // trip that we don't need since `tx` already has the row.
      ledger.addTransaction(tx);
      if (isFirstTransaction) {
        track("onboarding_step_completed", { step: "firstTransaction" });
      }
      // Flip the parse card to committed first; append the entry-moment
      // insight bubble after so it visually lands beneath the card.
      const committedMessages = state.messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "parse"
          ? { ...m, draft, committedTxId: tx.id, pendingSync: false }
          : m,
      );
      const insight = result.insight;
      if (insight) {
        setState({
          messages: [
            ...committedMessages,
            {
              id: newId("ai"),
              role: "assistant",
              kind: "insight",
              text: insight.text,
              severity: insight.severity,
            },
          ],
        });
      } else {
        setState({ messages: committedMessages });
      }
    } catch (err) {
      // Network failure (fetch threw — not an ApiError) → enqueue the
      // already-built body and mark the parse card as pending sync. The
      // drain on reconnect/mount replays it; Day 5's client_request_id
      // makes the replay idempotent, so a flurry of double-taps maps to
      // at most one committed row server-side. 4xx/5xx fall through to
      // the existing error bubble — the user can retry directly.
      if (!(err instanceof ApiError)) {
        const ownerUserId = useAppStore.getState().user?.id;
        if (!ownerUserId) {
          // Defensive: the chat UI requires an authed turn to render a
          // parse card, so this branch shouldn't be reachable. Surface
          // a soft error rather than enqueuing under a null owner.
          // eslint-disable-next-line no-console
          console.error("commitDraft: cannot enqueue, not signed in", err);
          appendAssistantText(
            "you need to be signed in to save transactions.",
          );
          return;
        }
        const { enqueue } = await import("./offline_queue");
        await enqueue({
          ownerUserId,
          kind: "transaction",
          payload: body,
          messageId: msgId,
        });
        setState({
          messages: state.messages.map((m) =>
            m.id === msgId && m.role === "assistant" && m.kind === "parse"
              ? { ...m, draft, pendingSync: true }
              : m,
          ),
        });
        return;
      }
      // 4xx / 5xx — surface the existing error. The user can retry.
      // eslint-disable-next-line no-console
      console.error("commitDraft → /transactions/confirm failed", err);
      appendAssistantText(
        "couldn't save that transaction. try again in a moment.",
      );
    }
  },

  /**
   * Commit a card parse draft to the backend.
   *
   * Day 15: cards now carry a `client_request_id` (see DESIGN.md §8.1 and
   * migration `20260517120000_cards_client_request_id.sql`). It's a stable
   * per-proposal join key — server-minted at `propose_card`, posted back
   * here, persisted on the row. `/cards/confirm` short-circuits on
   * same-crid replay (returns the existing row), so a network retry of
   * the same proposal is harmless. The structural dedup (partial unique
   * index on issuer + last_four) still owns the "same physical card"
   * case and surfaces 409 `active_card_exists` for different-crid /
   * same-card collisions.
   */
  async commitCardDraft(msgId: string, draft: CardParseDraft | null) {
    if (!draft) return;
    if (draft.issuer === null || draft.network === null) {
      appendAssistantText(
        "i need an issuer and network on this card before adding it.",
      );
      return;
    }
    if (!/^\d{4}$/.test(draft.lastFour)) {
      appendAssistantText("i need the last 4 digits of the card.");
      return;
    }
    if (!draft.clientRequestId) {
      // Defensive: a draft without a crid can't be confirmed — the
      // backend annotation join would be impossible. Shouldn't happen
      // with the wired chat (propose_card always emits it now), so flag
      // it instead of silently dropping the tap.
      appendAssistantText(
        "this card draft is missing its proposal id — try asking again.",
      );
      return;
    }

    const body: CardProposal = {
      network: draft.network,
      last_four: draft.lastFour,
      name: draft.name,
      issuer: draft.issuer,
      program: draft.program,
      multipliers: draft.multipliers,
      base_reward_rate: draft.baseRewardRate ?? null,
      rewards_currency: draft.rewardsCurrency ?? null,
      // Send the lookup region so an `other`-issuer card keeps it (confirm
      // pins a known issuer's region server-side and ignores this).
      region: draft.region ?? null,
      annual_fee: draft.annualFee,
      // Day 19b — when present alongside a non-zero annual_fee, the
      // confirm route's `insert_card_with_af` RPC creates a companion
      // AF subscription atomically. Null/undefined means no AF
      // tracking is set up at create time.
      next_annual_fee_date: draft.nextAnnualFeeDate ?? null,
      source_urls: draft.sourceUrls,
      alias: draft.alias ?? null,
      needs_manual: draft.needsManual,
      client_request_id: draft.clientRequestId,
    };

    try {
      const card = await confirmCard(body);
      ledger.addCard(card);
      track("feature_used", { feature: "card_added" });
      const committedMessages = state.messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "card-parse"
          ? { ...m, draft, committedCardId: card.id, pendingSync: false }
          : m,
      );
      setState({ messages: committedMessages });
    } catch (err) {
      if (isActiveCardExistsError(err)) {
        const detail = err.body.detail;
        // Flip the card to committed using the existing row's id so the
        // user can't keep re-tapping. The text crumb explains the no-op.
        const committedMessages = state.messages.map((m) =>
          m.id === msgId && m.role === "assistant" && m.kind === "card-parse"
            ? {
                ...m,
                draft,
                committedCardId: detail.existing_card_id,
                pendingSync: false,
              }
            : m,
        );
        setState({ messages: committedMessages });
        appendAssistantText(
          `you already have ${detail.existing_card_name} ending ${
            detail.existing_card_last_four ?? draft.lastFour
          } — edit that one from the cards page.`,
        );
        return;
      }
      // Network failure → enqueue the card confirm. Cards have no
      // client_request_id idempotency, but the partial unique index
      // `cards_active_identity_uniq` makes a duplicate POST return 409
      // active_card_exists, which the drain treats as a silent dequeue
      // (see offline_queue._drainOne). 4xx/5xx fall through to the
      // existing error path.
      if (!(err instanceof ApiError)) {
        const ownerUserId = useAppStore.getState().user?.id;
        if (!ownerUserId) {
          // eslint-disable-next-line no-console
          console.error(
            "commitCardDraft: cannot enqueue, not signed in",
            err,
          );
          appendAssistantText(
            "you need to be signed in to add cards.",
          );
          return;
        }
        const { enqueue } = await import("./offline_queue");
        await enqueue({
          ownerUserId,
          kind: "card",
          payload: body,
          messageId: msgId,
        });
        setState({
          messages: state.messages.map((m) =>
            m.id === msgId && m.role === "assistant" && m.kind === "card-parse"
              ? { ...m, draft, pendingSync: true }
              : m,
          ),
        });
        return;
      }
      // eslint-disable-next-line no-console
      console.error("commitCardDraft → /cards/confirm failed", err);
      appendAssistantText("couldn't add that card. try again in a moment.");
    }
  },

  /**
   * Commit a subscription parse draft to the backend — Day 19.
   *
   * Shape mirrors `commitDraft` / `commitCardDraft`: validate the
   * draft, build the SubscriptionProposal body, POST, flip the
   * parse card to committed. On a thrown `ApiError` we surface a
   * user-facing message; on a network failure (non-ApiError) we
   * enqueue under `kind: "subscription"` so the offline queue
   * drains it on reconnect. The crid-based idempotency on the
   * server side makes a drain retry of an already-committed proposal
   * return the existing row rather than 23505.
   */
  async commitSubscriptionDraft(
    msgId: string,
    draft: SubscriptionParseDraft | null,
  ) {
    if (!draft) return;
    if (!draft.clientRequestId) {
      // Defensive — the backend annotation join would be impossible.
      appendAssistantText(
        "this subscription draft is missing its proposal id — try asking again.",
      );
      return;
    }
    if (!/^\d+(?:\.\d{1,2})?$/.test(draft.amount.trim())) {
      appendAssistantText(
        "amount has to be a positive number with up to two decimals.",
      );
      return;
    }

    const body: SubscriptionProposal = {
      name: draft.name,
      amount: draft.amount,
      frequency: draft.frequency,
      start_date: draft.startDate,
      next_billing_date: draft.nextBillingDate,
      category: draft.category,
      card_id: draft.cardId,
      client_request_id: draft.clientRequestId,
    };

    try {
      const sub = await confirmSubscription(body);
      addSubscriptionLocal(sub);
      track("feature_used", { feature: "subscription_added" });
      const committedMessages = state.messages.map((m) =>
        m.id === msgId &&
        m.role === "assistant" &&
        m.kind === "subscription-parse"
          ? {
              ...m,
              draft,
              committedSubscriptionId: sub.id,
              committedState: sub.status,
              pendingSync: false,
            }
          : m,
      );
      setState({ messages: committedMessages });
    } catch (err) {
      if (!(err instanceof ApiError)) {
        const ownerUserId = useAppStore.getState().user?.id;
        if (!ownerUserId) {
          // eslint-disable-next-line no-console
          console.error(
            "commitSubscriptionDraft: cannot enqueue, not signed in",
            err,
          );
          appendAssistantText(
            "you need to be signed in to track subscriptions.",
          );
          return;
        }
        const { enqueue } = await import("./offline_queue");
        await enqueue({
          ownerUserId,
          kind: "subscription",
          payload: body,
          messageId: msgId,
        });
        setState({
          messages: state.messages.map((m) =>
            m.id === msgId &&
            m.role === "assistant" &&
            m.kind === "subscription-parse"
              ? { ...m, draft, pendingSync: true }
              : m,
          ),
        });
        return;
      }
      // eslint-disable-next-line no-console
      console.error(
        "commitSubscriptionDraft → /subscriptions/confirm failed",
        err,
      );
      appendAssistantText(
        "couldn't track that subscription. try again in a moment.",
      );
    }
  },

  /* ─── Offline drain hooks (called from offline_queue.ts) ───────── */

  /**
   * Drain handler for a 2xx `POST /transactions/confirm` from a queued
   * entry. Finds the matching in-memory parse card by `client_request_id`
   * (preferred — survives a same-session rehydrate where the message id
   * regenerates) or by `messageId` (fallback for legacy entries), patches
   * its draft to the response's actual field values, and appends the
   * entry-moment insight bubble.
   *
   * Idempotent on no-match: if the user closed the original chat thread,
   * or the rehydrated message is still pending its `committed_payload`
   * annotation, we still inject the row into the ledger (the dashboard
   * needs it) and surface the insight as a standalone bubble. The next
   * page reload will render the parse card with its committed_payload-
   * driven `logged.` state.
   */
  applyDrainTxSuccess(
    match: { clientRequestId: string; messageId?: string },
    result: ConfirmTransactionResult,
  ): void {
    const tx = result.transaction;
    ledger.addTransaction(tx);
    const next = state.messages.map((m) => {
      if (m.role !== "assistant" || m.kind !== "parse") return m;
      const isMatch =
        (!!m.draft.clientRequestId &&
          m.draft.clientRequestId === match.clientRequestId) ||
        (!!match.messageId && m.id === match.messageId);
      if (!isMatch) return m;
      return {
        ...m,
        draft: _txToDraft(tx, m.draft),
        committedTxId: tx.id,
        committedState: "active" as const,
        pendingSync: false,
        // Unfreeze: if this message was rehydrated as a read-only
        // historical artifact, the drain commit makes it the live truth
        // now. Leaving `frozen=true` would keep the buttons hidden, which
        // is desired — but the `committed` rendering takes priority
        // anyway, so clearing `frozen` is just hygiene.
        frozen: false,
      };
    });
    setState({
      messages: result.insight
        ? [
            ...next,
            {
              id: newId("ai"),
              role: "assistant",
              kind: "insight",
              text: result.insight.text,
              severity: result.insight.severity,
            },
          ]
        : next,
    });
  },

  /**
   * Drain handler for a 2xx `POST /cards/confirm` from a queued entry.
   * Cards have no `client_request_id`, so we match by the in-memory
   * message id captured at enqueue time. If the id no longer matches
   * (e.g., the chat thread rehydrated since), the in-memory patch is
   * a no-op — `_annotate_committed_proposals` on the next /chat/messages
   * fetch will paint the rehydrated card with `committed_payload`.
   */
  applyDrainCardSuccess(
    match: { clientRequestId?: string; messageId?: string },
    card: CardRow,
  ): void {
    ledger.addCard(card);
    const idx = _findCardParseTarget(
      state.messages,
      match.clientRequestId ?? card.client_request_id,
      match.messageId,
      card.name,
    );
    if (idx === -1) return;
    setState({
      messages: state.messages.map((m, i) =>
        i === idx && m.role === "assistant" && m.kind === "card-parse"
          ? {
              ...m,
              draft: _cardRowToDraft(card),
              committedCardId: card.id,
              committedState: "active" as const,
              pendingSync: false,
              frozen: false,
            }
          : m,
      ),
    });
  },

  /**
   * Drain handler for a 2xx `POST /subscriptions/confirm` from a queued
   * entry (Day 19). Lands the new row in the subscriptions store for
   * immediate render on `/subscriptions`, and flips any matching
   * in-memory subscription-parse card to its committed state — same
   * shape as the transaction success hook.
   *
   * Match priority mirrors the cards path: crid first (rehydrate-stable
   * join key), messageId second (legacy same-session fallback). No
   * analog of the card 409 path: subscriptions dedup via
   * `client_request_id` (a replay returns the existing row with 2xx,
   * not 409), so a successful drain is the only terminal-success
   * outcome.
   */
  applyDrainSubscriptionSuccess(
    match: { clientRequestId: string; messageId?: string },
    subscription: SubscriptionRow,
  ): void {
    addSubscriptionLocal(subscription);
    const next = state.messages.map((m) => {
      if (m.role !== "assistant" || m.kind !== "subscription-parse") return m;
      const isMatch =
        (!!m.draft.clientRequestId &&
          m.draft.clientRequestId === match.clientRequestId) ||
        (!!match.messageId && m.id === match.messageId);
      if (!isMatch) return m;
      return {
        ...m,
        draft: _subscriptionRowToDraft(subscription, m.draft),
        committedSubscriptionId: subscription.id,
        committedState: subscription.status,
        pendingSync: false,
        frozen: false,
      };
    });
    setState({ messages: next });
  },

  /**
   * Drain handler for a 409 `active_card_exists` on a queued card
   * confirm. The card is already in the wallet (a prior drain attempt
   * landed, or a separate session committed it). Silent dequeue — flip
   * the matching message to committed using the existing row's id; do
   * NOT append the "you already have this card" text bubble that the
   * synchronous `commitCardDraft` 409 path renders, because the user's
   * confirm tap happened a while ago and the live error copy would be
   * confusing.
   */
  applyDrainCardConflict(
    match: { clientRequestId?: string; messageId?: string },
    detail: ActiveCardExistsDetail,
  ): void {
    // 409 detail doesn't carry crid (the EXISTING row has a different
    // crid than this proposal — that's why we collided on the natural
    // key). So we match by the *queued proposal's* crid against the
    // in-memory draft. The conflict-detail name is the last-resort
    // fallback for legacy proposals.
    const idx = _findCardParseTarget(
      state.messages,
      match.clientRequestId,
      match.messageId,
      detail.existing_card_name,
    );
    if (idx === -1) return;
    setState({
      messages: state.messages.map((m, i) =>
        i === idx && m.role === "assistant" && m.kind === "card-parse"
          ? {
              ...m,
              committedCardId: detail.existing_card_id,
              committedState: "active" as const,
              pendingSync: false,
              frozen: false,
            }
          : m,
      ),
    });
  },

  /**
   * Drain handler for a 4xx (other than card 409) on a queued entry.
   * Server-side validation will never accept this payload (e.g., a 422
   * from a malformed body that slipped through client-side checks).
   *
   * Per Day 15 spec: pop the entry from the queue and re-surface the
   * proposal as a fixable parse card in the chat thread with a quiet
   * "couldn't sync" line. We do this by clearing `pendingSync` (so the
   * buttons return) and clearing `frozen` (so a rehydrated read-only
   * card becomes editable again), then appending a text bubble. The
   * user can edit and re-tap "looks right" (which will go straight to
   * the server now that we're online), or just leave it.
   *
   * If we can't find the matching message (e.g., it scrolled off in
   * another tab — single-active-device makes this unlikely but possible),
   * just append the error text bubble standalone so the user knows.
   */
  applyDrainPermanentFailure(
    entry: PersistedQueueEntry,
    err: ApiError,
  ): void {
    let matched = false;
    const next = state.messages.map((m) => {
      if (entry.kind === "transaction") {
        if (m.role !== "assistant" || m.kind !== "parse") return m;
        const isMatch =
          (!!m.draft.clientRequestId &&
            m.draft.clientRequestId === entry.payload.client_request_id) ||
          (!!entry.messageId && m.id === entry.messageId);
        if (!isMatch) return m;
        matched = true;
        return { ...m, pendingSync: false, frozen: false };
      }
      if (entry.kind === "subscription") {
        if (m.role !== "assistant" || m.kind !== "subscription-parse") {
          return m;
        }
        const cridMatch =
          !!m.draft.clientRequestId &&
          m.draft.clientRequestId === entry.payload.client_request_id;
        const idMatch = !cridMatch && !!entry.messageId && m.id === entry.messageId;
        if (!(cridMatch || idMatch)) return m;
        matched = true;
        return { ...m, pendingSync: false, frozen: false };
      }
      // card branch
      if (m.role !== "assistant" || m.kind !== "card-parse") return m;
      // crid is the rehydrate-stable join key (Day 15); messageId is the
      // same-session fallback; name is the legacy fallback for proposals
      // that pre-date the crid column.
      const cridMatch =
        !!entry.payload.client_request_id &&
        m.draft.clientRequestId === entry.payload.client_request_id;
      const idMatch = !cridMatch && !!entry.messageId && m.id === entry.messageId;
      const nameMatch =
        !cridMatch &&
        !idMatch &&
        !matched &&
        !m.committedCardId &&
        m.draft.name === entry.payload.name;
      if (!(cridMatch || idMatch || nameMatch)) return m;
      matched = true;
      return { ...m, pendingSync: false, frozen: false };
    });
    setState({
      messages: [
        ...next,
        {
          id: newId("ai"),
          role: "assistant",
          kind: "text",
          text: matched
            ? "this couldn't sync — fix or discard."
            : `couldn't sync a queued change (${err.status}).`,
        },
      ],
    });
  },

  /* Drawer controls — unchanged from the local mock. */
  openDrawer() {
    setState({ drawerOpen: true });
  },
  closeDrawer() {
    setState({ drawerOpen: false, drawerExpanded: false });
  },
  toggleExpanded() {
    setState({ drawerExpanded: !state.drawerExpanded });
  },
  sendFromComposer(raw: string) {
    void chatStore.send(raw);
    setState({ drawerOpen: true });
  },

  /**
   * Desktop-composer entry for a receipt photo — opens the drawer so the
   * user sees the parse card land, then runs the same scan as the mobile
   * path (`sendReceiptPhoto`). Mirrors `sendFromComposer` for images.
   */
  sendReceiptFromComposer(image: Blob) {
    void chatStore.sendReceiptPhoto(image);
    setState({ drawerOpen: true });
  },

  /** Helper: find tx by id (for candidate lookup). */
  findTx(id: string): Transaction | undefined {
    return ledger.getSnapshot().transactions.find((t) => t.id === id);
  },

  /**
   * Dev-only override for the daily-cap UI. Mirrors the sessionStorage
   * flag so the state survives a page reload during manual UI testing
   * — production cap state comes from `send()`'s 429 path instead.
   */
  setCapEngaged(next: boolean): void {
    setDailyCapEngaged(next);
    setState({ capEngaged: next });
  },

  /**
   * Mutate a parse card's draft in place — used by the "fix" flow when the
   * user tweaks fields in EditTransactionSheet before tapping confirm.
   * Only fields present in the patch are touched; everything else stays.
   * The wire-payload triple (clientRequestId / notes / geminiSuggestion)
   * is preserved so the eventual confirm still round-trips with the same
   * idempotency key.
   */
  updateDraft(msgId: string, patch: Partial<ParseDraft>): void {
    setState({
      messages: state.messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "parse"
          ? { ...m, draft: { ...m.draft, ...patch } }
          : m,
      ),
    });
  },

  /**
   * Discard a parse card without committing — used when the user opens the
   * edit sheet on a draft and taps delete. We replace the parse card with
   * a short text crumb so the conversation still reads cleanly.
   */
  discardDraft(msgId: string): void {
    setState({
      messages: state.messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "parse"
          ? {
              id: m.id,
              role: "assistant",
              kind: "text",
              text: "ok, dropped that one.",
            }
          : m,
      ),
    });
  },
};

export function useChatStore() {
  const [snap, setSnap] = useState<ChatState>(state);
  useEffect(
    () => chatStore.subscribe(() => setSnap(chatStore.getSnapshot())),
    [],
  );
  return snap;
}

/**
 * Run one streaming turn. Used by both `send()` (after appending the
 * user bubble) and `retry()` (which re-uses the existing user bubble).
 *
 * Keeps a closure-local `accumulated` string for the streamed text so
 * `onDone` can pass it to `_renderTurn` as `assistant_text` (Day 10
 * contract) — the store-level `streamingText` mirrors it for the UI
 * but is cleared on terminal events.
 */
async function _streamOnce(messageText: string): Promise<void> {
  setState({ busy: true, streamingText: "", lastError: null });

  // Offline check is cheap and avoids a confusing 0-status error path.
  // The PWA Service Worker doesn't proxy /chat (see vite.config.ts
  // navigateFallbackDenylist) so the browser will surface the failure
  // straight to fetch.
  const online = typeof navigator === "undefined" ? true : navigator.onLine;
  if (!online) {
    setState({
      busy: false,
      lastError: {
        message: "you're offline right now. retry when you reconnect.",
        pendingMessage: messageText,
      },
    });
    return;
  }

  let accumulated = "";

  await streamTurn({
    message: messageText,
    conversationId: state.conversationId,
    onToken: (delta) => {
      accumulated += delta;
      setState({ streamingText: accumulated });
    },
    onToolUse: (_payload) => {
      // No-op for v1. Per-tool pill ("looking up dining transactions…")
      // is a planned enhancement; today the busy pill says "thinking…"
      // and the streamingText bubble already feeds the user real-time
      // tokens.
    },
    onDone: (payload) => {
      const isFirstTurnOfSession =
        sessionMetrics.conversationId !== payload.conversation_id;
      if (state.conversationId !== payload.conversation_id) {
        writePersistedConvoId(payload.conversation_id);
      }
      if (state.capEngaged) setState({ capEngaged: false });
      setState({
        conversationId: payload.conversation_id,
        streamingText: "",
        lastError: null,
      });
      // Day 26 session metrics. A change in the resolved conversation_id
      // means we've crossed into a new conversation (first turn after
      // newChat() or app boot). Fire chat_session_started exactly once
      // per session; bump turn_count on every successful done.
      if (isFirstTurnOfSession) {
        sessionMetrics.conversationId = payload.conversation_id;
        sessionMetrics.startedAt = Date.now();
        sessionMetrics.turnCount = 0;
        track("chat_session_started", {
          conversation_id: payload.conversation_id,
        });
      }
      sessionMetrics.turnCount += 1;
      _renderTurn({
        conversation_id: payload.conversation_id,
        assistant_text: accumulated,
        tool_calls: payload.tool_calls,
      });
    },
    onError: (err) => {
      _handleStreamError(err, messageText);
    },
  });

  setState({ busy: false });
}

/**
 * Map a terminal SSE error into store state. Three categories:
 *
 *   - DAILY_CAP_EXCEEDED → latch capEngaged; the InputRow swaps to
 *     <DailyCapCard />. No retry CTA — the user can't retry until the
 *     UTC bucket rolls over.
 *   - DEVICE_DISPLACED / MISSING_DEVICE_ID → chat_stream already
 *     engaged the global displacement modal via useAppStore. Nothing
 *     more for chat to do.
 *   - everything else (AI_PROVIDER_RATE_LIMITED, LOOP_LIMIT,
 *     STREAM_INCOMPLETE, NETWORK, PERSISTENCE_FAILED, …) → latch
 *     lastError with a friendly message + the pendingMessage to
 *     re-fire on retry. The partial streamingText is discarded so the
 *     thread reads cleanly after retry succeeds.
 */
function _handleStreamError(err: StreamError, pendingMessage: string): void {
  setState({ streamingText: "" });
  if (err.code === "DAILY_CAP_EXCEEDED") {
    setState({ capEngaged: true });
    return;
  }
  if (err.code === "DEVICE_DISPLACED" || err.code === "MISSING_DEVICE_ID") {
    // Global modal owns this; the chat UI just clears the busy flag.
    return;
  }
  const friendly = _friendlyErrorMessage(err);
  setState({ lastError: { message: friendly, pendingMessage } });
}

function _friendlyErrorMessage(err: StreamError): string {
  switch (err.code) {
    case "AI_PROVIDER_RATE_LIMITED":
      return "our ai is having a moment. try again in a few minutes.";
    case "LOOP_LIMIT":
      return "something went wrong on our end. try rephrasing?";
    case "PERSISTENCE_FAILED":
      return "couldn't save that turn — try again.";
    case "STREAM_INCOMPLETE":
    case "NETWORK":
      return "connection lost mid-reply. retry?";
    default:
      return "couldn't reach the chat. check your connection?";
  }
}

/* ────────────────────────────────────────────────────────────────────
 * Turn → message rendering
 *
 * Each turn returns a final assistant_text plus a list of tool_calls in
 * the order the loop made them. We split them into rendered messages:
 *
 *   - Each `propose_transaction` becomes its own parse card.
 *   - Read tools (`get_*`, `calculate_total`, `set_goal`) surface as a
 *     `via` attribution chip carrying the raw backend tool name; the
 *     chip's renderer (MessageBubble.tsx) maps known names to friendly
 *     labels. If the agent emitted multiple, the first non-renderer
 *     wins for the chip — the full list remains in chat_turn_trace.
 *   - The final `assistant_text` becomes a text bubble unless it's been
 *     consumed as the preface of a parse card (the common case where the
 *     agent only proposes and says nothing else useful).
 *
 * If the agent returned nothing actionable, we still append a generic
 * "ok" bubble so the user sees that the turn completed.
 * ──────────────────────────────────────────────────────────────────── */

function _renderTurn(res: ChatTurnResponse): void {
  const proposeCalls = res.tool_calls.filter(
    (tc) => tc.name === "propose_transaction",
  );
  const proposeCardCalls = res.tool_calls.filter(
    (tc) => tc.name === "propose_card",
  );
  const proposeSubCalls = res.tool_calls.filter(
    (tc) => tc.name === "propose_subscription",
  );
  const getTransactionsCalls = res.tool_calls.filter(
    (tc) => tc.name === "get_transactions",
  );
  const renderChartCalls = res.tool_calls.filter(
    (tc) => tc.name === "render_chart",
  );
  // Pick a non-propose, non-get_transactions, non-render_chart tool for the
  // attribution chip on the trailing text bubble (the dedicated renderers
  // already attribute themselves). First wins.
  const otherToolName = res.tool_calls.find(
    (tc) =>
      tc.name !== "propose_transaction" &&
      tc.name !== "propose_card" &&
      tc.name !== "propose_subscription" &&
      tc.name !== "get_transactions" &&
      tc.name !== "render_chart",
  )?.name;

  const drafted: ChatMessage[] = [];
  let textConsumed = false;

  // The agent's prose typically introduces the parse card ("got it...").
  // Use it as the preface on the FIRST parse card so we don't end up with
  // a redundant text-then-card pair.
  for (let i = 0; i < proposeCalls.length; i++) {
    const draft = _proposalToDraft(proposeCalls[i]);
    if (!draft) continue;
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "parse",
      preface:
        i === 0
          ? res.assistant_text || "got it. does this look right?"
          : undefined,
      draft,
    });
    if (i === 0) textConsumed = true;
  }

  // Card proposals get the same preface-consumes-text treatment, but only
  // when no transaction proposal already consumed it (the propose order
  // wins for the bubble preface). Cards are never multi-call in a single
  // turn under the v1 prompt; the loop tolerates N defensively anyway.
  for (let i = 0; i < proposeCardCalls.length; i++) {
    const draft = _proposalToCardDraft(proposeCardCalls[i]);
    if (!draft) continue;
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "card-parse",
      preface:
        i === 0 && !textConsumed
          ? res.assistant_text || "got it. does this look right?"
          : undefined,
      draft,
    });
    if (i === 0 && !textConsumed) textConsumed = true;
  }

  // Subscription proposals — same preface-consumes-text shape as the
  // others, deferring to any earlier propose_* call for the assistant
  // text. Subscriptions don't multi-call in a single turn under the v1
  // prompt either; N is tolerated.
  for (let i = 0; i < proposeSubCalls.length; i++) {
    const draft = _proposalToSubscriptionDraft(proposeSubCalls[i]);
    if (!draft) continue;
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "subscription-parse",
      preface:
        i === 0 && !textConsumed
          ? res.assistant_text || "got it. does this look right?"
          : undefined,
      draft,
    });
    if (i === 0 && !textConsumed) textConsumed = true;
  }

  // Render get_transactions as a candidate list — the agent typically uses
  // it for "show me / find / delete the X" intents. We injects the returned
  // rows into the ledger so CandidateCards' findTx(id) lookup resolves and
  // the dashboard learns about any older rows the initial /transactions
  // fetch hadn't surfaced.
  //
  // Intent inference is regex-based on the assistant's prose — cheap and
  // wrong sometimes, but the candidate cards' tap target still leads to the
  // edit sheet which has its own delete affordance, so a misclassified
  // intent is a chip-color mistake, not a broken flow.
  const candidateIntent: "edit" | "delete" = /\b(delete|remove|drop)\b/i.test(
    res.assistant_text,
  )
    ? "delete"
    : "edit";
  for (let i = 0; i < getTransactionsCalls.length; i++) {
    const txs = _ingestTransactionRows(getTransactionsCalls[i]);
    if (txs.length === 0) continue;
    const preface =
      i === 0 && res.assistant_text
        ? res.assistant_text
        : i === 0
          ? "here are the matches:"
          : "more matches:";
    if (i === 0 && res.assistant_text) textConsumed = true;
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "candidates",
      preface,
      candidateIds: txs.map((t) => t.id),
      intent: candidateIntent,
      via: "get_transactions",
    });
  }

  // Pick the underlying data tool the chart was built from so the chip
  // says "via spending summary" rather than "via chart" — the user cares
  // where the numbers came from, not that the agent then formatted it.
  // Falls back to `render_chart` if no data tool ran in the same turn.
  const chartDataToolName = res.tool_calls.find(
    (tc) =>
      tc.name === "get_spending_summary" ||
      tc.name === "calculate_total" ||
      tc.name === "get_transactions",
  )?.name ?? "render_chart";

  // Render any render_chart calls. The agent's system prompt says one
  // chart per turn, but we tolerate N defensively — they stack vertically
  // in the thread. The first chart consumes the assistant_text as a
  // preface (same pattern as parse/candidates above) so the bubble above
  // the chart isn't a duplicate of the title.
  for (let i = 0; i < renderChartCalls.length; i++) {
    const spec = _toolToChartSpec(renderChartCalls[i]);
    if (!spec) continue;
    const preface =
      i === 0 && res.assistant_text && !textConsumed
        ? res.assistant_text
        : undefined;
    if (i === 0 && preface) textConsumed = true;
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "rich-chart",
      preface,
      spec,
      via: chartDataToolName,
    });
  }

  // Surface the final text bubble only when no dedicated renderer consumed
  // it. This avoids the "agent says 'here are your dining transactions:'
  // immediately above a list that shows them" duplication.
  if (res.assistant_text && !textConsumed) {
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "text",
      text: res.assistant_text,
      via: otherToolName,
    });
  }

  if (drafted.length === 0) {
    drafted.push({
      id: newId("ai"),
      role: "assistant",
      kind: "text",
      text: "ok.",
    });
  }

  setState({ messages: [...state.messages, ...drafted] });
}

/**
 * Pull TransactionRow-shaped items out of a get_transactions tool result,
 * map them to the UI shape, and inject any not-yet-known rows into the
 * ledger so the candidate-card lookup resolves. Returns the mapped rows
 * in input order so the message keeps the agent's chosen sort.
 */
function _ingestTransactionRows(call: ChatToolCall): Transaction[] {
  const r = call.result;
  if (!r || typeof r !== "object" || !Array.isArray(r.items)) return [];
  const rows = r.items as unknown as TransactionRowWire[];
  const mapped = rows
    .map((row) => {
      if (!row || typeof row.id !== "string") return null;
      // The wire `user_id` field is stripped server-side for the agent tool
      // path; fromWire doesn't read it, so the missing key is fine.
      return fromWire(row);
    })
    .filter((t): t is Transaction => t !== null);
  if (mapped.length === 0) return [];
  const known = new Set(ledger.getSnapshot().transactions.map((t) => t.id));
  for (const tx of mapped) {
    if (!known.has(tx.id)) ledger.addTransaction(tx);
  }
  return mapped;
}

/**
 * Map a propose_transaction tool result (TransactionProposal-shaped dict
 * — see app/models/transactions.py) into the local ParseDraft the UI was
 * built around. Confidence is hardcoded high since the backend has
 * already validated the proposal; the per-field pencils stay quiet
 * unless the user wants to edit. Returns null if the payload doesn't
 * carry the minimum fields (defensive — should never happen given the
 * server's response_model).
 */
function _proposalToDraft(
  call: ChatToolCall,
  committedPayload?: unknown,
): ParseDraft | null {
  // Backend response_model is TransactionProposal (app/models/transactions.py)
  // and the local Category union now covers every backend value, so we
  // trust the wire shape. Day 10b §1: "if a server row still returns
  // something outside the union it's a real bug, not a cast to paper over."
  //
  // Day 15: when the synthetic block carries `committed_payload` (the live
  // `transactions` row's user-editable fields, stitched on by
  // `_annotate_committed_proposals`), merge it OVER `call.result` so the
  // rehydrated draft reflects what was actually committed — not the
  // agent's original suggestion. The proposal-only fields the row doesn't
  // carry (`gemini_suggestion`, and `client_request_id` itself echoed via
  // result) come through from `call.result` via the spread fallback.
  const committed =
    committedPayload && typeof committedPayload === "object"
      ? (committedPayload as Record<string, unknown>)
      : null;
  const merged = {
    ...(call.result as Record<string, unknown>),
    ...(committed ?? {}),
  };
  return _wireProposalToDraft(merged as unknown as TransactionProposalWire);
}

/**
 * Map a `TransactionProposal` wire object (from `propose_transaction` OR
 * `POST /receipts/parse`) into the local `ParseDraft`. Shared by the chat path
 * (`_proposalToDraft`, after its committed-payload merge) and the receipt path
 * (`sendReceiptPhoto`) so both create surfaces produce identical drafts.
 * Confidence is hardcoded high — the backend already validated the proposal —
 * so the per-field pencils stay quiet unless the user chooses to edit. Returns
 * null if the payload lacks the load-bearing fields (defensive).
 */
function _wireProposalToDraft(r: TransactionProposalWire): ParseDraft | null {
  const merchant = typeof r.merchant === "string" ? r.merchant : null;
  const date = typeof r.date === "string" ? r.date : null;
  const category = typeof r.category === "string" ? r.category : null;
  const clientRequestId =
    typeof r.client_request_id === "string" ? r.client_request_id : null;
  if (!merchant || !date || !category || !clientRequestId) return null;

  const amountRaw = r.amount;
  const amountNum =
    typeof amountRaw === "number"
      ? amountRaw
      : typeof amountRaw === "string"
        ? Number(amountRaw)
        : NaN;
  if (!Number.isFinite(amountNum) || amountNum <= 0) return null;
  const amountCents = Math.round(amountNum * 100);

  return {
    merchant,
    amountCents,
    date,
    cardId: typeof r.card_id === "string" ? r.card_id : "",
    category,
    confidence: {
      merchant: 0.95,
      amount: 0.95,
      date: 0.95,
      card: 0.95,
      category: 0.95,
    },
    clientRequestId,
    notes: typeof r.notes === "string" ? r.notes : null,
    geminiSuggestion:
      typeof r.gemini_suggestion === "string" ? r.gemini_suggestion : null,
    source: r.source === "receipt_photo" ? "receipt_photo" : "nlp",
  };
}

/**
 * Friendly one-liner for a receipt-scan failure. Maps the backend HTTP codes
 * (413 too large, 503 Gemini down, 422/other unreadable) to plain copy, with a
 * network-error fallback. English-only, consistent with the store's other
 * operational messages — chat chrome is i18n'd in the components, not here.
 */
function _receiptErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 413) return "that photo is too large — try again.";
    if (err.status === 503) {
      return "receipt scanning is briefly unavailable — try again in a moment, or type it.";
    }
    return "couldn't read that receipt. try a clearer photo, or just type it.";
  }
  return "couldn't scan that receipt — check your connection and try again.";
}

/**
 * Map a propose_card tool result (CardProposal-shaped dict — see
 * app/models/cards.py) into the local CardParseDraft. Day 14 cards have no
 * client_request_id idempotency, so the draft only carries the
 * commit-shaped fields the user can tweak before tapping "looks right."
 * Returns null when the payload's missing the load-bearing fields (name
 * is required at minimum — without it the UI can't render a card
 * headline).
 */
interface CardProposalWire {
  network: CardNetwork | null;
  last_four: string | null;
  name: string;
  issuer: CardIssuer | null;
  program: CardProgram;
  multipliers: Record<string, number>;
  base_reward_rate?: string | number | null;
  rewards_currency?: string | null;
  region?: CardRegion | null;
  client_request_id?: string;
  annual_fee: string | number | null;
  next_annual_fee_date?: string | null;
  source_urls: string[];
  alias: string | null;
  needs_manual: boolean;
}

function _proposalToCardDraft(
  call: ChatToolCall,
  committedPayload?: unknown,
): CardParseDraft | null {
  // Day 15: when `committed_payload` is present (live `cards` row), prefer
  // its values over the proposal's. `needs_manual` only exists on the
  // proposal — preserved via the spread fallback.
  const committed =
    committedPayload && typeof committedPayload === "object"
      ? (committedPayload as Record<string, unknown>)
      : null;
  const merged = {
    ...((call.result as Record<string, unknown>) ?? {}),
    ...(committed ?? {}),
  };
  const r = merged as unknown as CardProposalWire;
  if (!r || typeof r !== "object") return null;
  if (typeof r.name !== "string" || !r.name) return null;

  // Backend defaults network to "other" and issuer to "other" when the
  // lookup couldn't determine them (see propose_card in app/agent/tools.py).
  // Surface that as null on the local draft so the CardParseCard renders
  // the unresolved "select…" state with a warn ring, matching AddCardStep's
  // posture — silently defaulting both to "other" would let the user save
  // wrong identity metadata and trip the (user_id, issuer, last_four)
  // unique index on the next add of a real card with the same last 4.
  //
  // Committed rows are exempt: `committed_payload` omits `needs_manual`
  // (a proposal-time annotation), so the spread fallback resurrects the
  // proposal's `needs_manual: true` — and a card the user deliberately
  // confirmed with issuer "other" would be nulled back to the unresolved
  // picker state on rehydrate (audit P3-33). A committed row's identity
  // fields are settled facts; render them as-is.
  const isCommitted = committed != null;
  const issuer =
    !isCommitted && r.issuer === "other" && r.needs_manual ? null : r.issuer ?? null;
  const network =
    !isCommitted && r.network === "other" && r.needs_manual ? null : r.network ?? null;

  const annualFee =
    r.annual_fee === null || r.annual_fee === undefined
      ? null
      : typeof r.annual_fee === "string"
        ? r.annual_fee
        : String(r.annual_fee);

  return {
    name: r.name,
    issuer,
    network,
    program: r.program ?? "Other",
    multipliers:
      r.multipliers && typeof r.multipliers === "object" ? r.multipliers : {},
    baseRewardRate:
      r.base_reward_rate === null || r.base_reward_rate === undefined
        ? null
        : String(r.base_reward_rate),
    rewardsCurrency:
      typeof r.rewards_currency === "string" ? r.rewards_currency : null,
    region:
      r.region === "US" || r.region === "JP" || r.region === "TW"
        ? r.region
        : null,
    annualFee,
    sourceUrls: Array.isArray(r.source_urls) ? r.source_urls : [],
    lastFour: typeof r.last_four === "string" ? r.last_four : "",
    needsManual: r.needs_manual === true,
    alias: typeof r.alias === "string" ? r.alias : null,
    // Day 19b — propagate any date Claude extracted from the user's
    // mention (e.g. "AF renews March 15"). Null when neither the agent
    // nor a committed_payload supplied one; the parse card lets the
    // user set or clear it before confirming.
    nextAnnualFeeDate:
      typeof r.next_annual_fee_date === "string"
        ? r.next_annual_fee_date
        : null,
    clientRequestId:
      typeof r.client_request_id === "string" ? r.client_request_id : undefined,
  };
}

/**
 * Map a propose_subscription tool result (SubscriptionProposal-shaped
 * dict — see app/models/subscriptions.py) into the local
 * SubscriptionParseDraft the UI renders. `frequency` and `start_date`
 * carry the values the tool computed (forward-only-clamped
 * `next_billing_date` lives alongside `start_date`) so the parse card
 * can surface "first auto-log on {date} — today's charge isn't
 * backfilled" to set user expectations.
 *
 * Day 19: when `committedPayload` is present (live `subscriptions`
 * row), prefer its values over the proposal's — same shape as the
 * propose_card path. Returns null on missing load-bearing fields
 * (name + amount + frequency + client_request_id all required).
 */
interface SubscriptionProposalWire {
  name: string;
  amount: string | number;
  frequency: SubFrequency;
  start_date: string;
  next_billing_date: string;
  category: Category;
  card_id: string | null;
  client_request_id: string;
}

function _proposalToSubscriptionDraft(
  call: ChatToolCall,
  committedPayload?: unknown,
): SubscriptionParseDraft | null {
  const committed =
    committedPayload && typeof committedPayload === "object"
      ? (committedPayload as Record<string, unknown>)
      : null;
  const merged = {
    ...((call.result as Record<string, unknown>) ?? {}),
    ...(committed ?? {}),
  };
  const r = merged as unknown as SubscriptionProposalWire;
  if (!r || typeof r !== "object") return null;
  if (typeof r.name !== "string" || !r.name) return null;
  if (typeof r.client_request_id !== "string") return null;
  const amount =
    typeof r.amount === "string" ? r.amount : String(r.amount ?? "");
  return {
    name: r.name,
    amount,
    frequency: r.frequency,
    startDate: r.start_date,
    nextBillingDate: r.next_billing_date,
    category: r.category,
    cardId: typeof r.card_id === "string" ? r.card_id : null,
    clientRequestId: r.client_request_id,
  };
}

/**
 * Project a freshly-committed transaction row into the local ParseDraft
 * shape, preserving wire-payload bookkeeping fields from the original
 * draft (`clientRequestId`, `notes`, `geminiSuggestion`) that the row
 * doesn't carry back. Used by `applyDrainTxSuccess` to flip a queued
 * parse card's displayed values to the row's actual values — handles
 * the edit-before-tap case (user changed $40 → $42 offline; the row is
 * now $42, the rehydrated draft must show $42).
 */
/**
 * Project a freshly-committed subscription row into the local
 * SubscriptionParseDraft, preserving the draft's `clientRequestId`
 * (which the row also carries — they should match — but the spread
 * keeps it explicit). Mirrors `_txToDraft` for transactions.
 */
function _subscriptionRowToDraft(
  sub: SubscriptionRow,
  base: SubscriptionParseDraft,
): SubscriptionParseDraft {
  // The server-side category is enforced against ALLOWED_CATEGORIES, so
  // it always coerces to the local Category union. If we ever loosen
  // that enforcement (we won't), this cast becomes the documented spot
  // to validate.
  return {
    ...base,
    name: sub.name,
    amount: sub.amount,
    frequency: sub.frequency,
    startDate: sub.start_date,
    nextBillingDate: sub.next_billing_date,
    category: sub.category as Category,
    cardId: sub.card_id,
    clientRequestId: sub.client_request_id ?? base.clientRequestId,
  };
}

function _txToDraft(tx: Transaction, base: ParseDraft): ParseDraft {
  return {
    ...base,
    merchant: tx.merchant,
    amountCents: tx.amountCents,
    date: tx.date,
    cardId: tx.cardId,
    category: tx.category,
  };
}

/**
 * Locate the in-memory `card-parse` message a drain outcome should
 * patch. Match priority:
 *
 *   1. **`clientRequestId`** — the stable per-proposal join key from
 *      `propose_card`. Persisted on the rehydrated draft (via both
 *      `result.client_request_id` and `committed_payload.client_request_id`),
 *      so it survives a close-and-reopen cycle where the React message
 *      id was regenerated. 1:1 with the queue entry's
 *      `payload.client_request_id`. This is the load-bearing key.
 *   2. **`messageId`** — the in-memory id captured at enqueue time.
 *      Covers legacy entries (rare, only matters if an upgrade-time
 *      queued entry pre-dates the crid plumbing) and is a cheap
 *      same-session fallback.
 *   3. **`name`** — last-resort fallback for two-same-name cards in
 *      pre-Day-15 chat history whose proposal blocks don't have a
 *      crid (legacy). Only considers uncommitted cards; returns the
 *      first match. New proposals never hit this branch.
 *
 * Returns `-1` if no key matches. The drain treats that as a silent
 * no-op — the row is still in the wallet, and the next /chat/messages
 * rehydrate paints the card via the backend `committed_payload`
 * annotation (matched server-side by crid).
 */
function _findCardParseTarget(
  messages: ChatMessage[],
  clientRequestId: string | undefined,
  messageId: string | undefined,
  cardName: string | undefined,
): number {
  if (clientRequestId) {
    const idx = messages.findIndex(
      (m) =>
        m.role === "assistant" &&
        m.kind === "card-parse" &&
        m.draft.clientRequestId === clientRequestId,
    );
    if (idx !== -1) return idx;
  }
  if (messageId) {
    const idx = messages.findIndex(
      (m) =>
        m.role === "assistant" &&
        m.kind === "card-parse" &&
        m.id === messageId,
    );
    if (idx !== -1) return idx;
  }
  if (!cardName) return -1;
  return messages.findIndex(
    (m) =>
      m.role === "assistant" &&
      m.kind === "card-parse" &&
      !m.committedCardId &&
      m.draft.name === cardName,
  );
}

/**
 * Project a freshly-committed card row into the local CardParseDraft.
 * Mirrors `_proposalToCardDraft` field-for-field but consumes the
 * narrower `CardRow` shape. `needsManual` always becomes false — a
 * committed card by definition has its identity fields resolved.
 */
function _cardRowToDraft(card: CardRow): CardParseDraft {
  // `next_annual_fee_date` lives on the companion AF subscription's
  // `next_billing_date`, not on `cards`. The rehydrated chat parse
  // card doesn't surface it — the user inspects/edits the AF date on
  // the cards-page chip + EditCardAfSheet post-commit. Set null so
  // the draft type is satisfied without a phantom value.
  return {
    name: card.name,
    issuer: card.issuer,
    network: card.network,
    program: card.program,
    multipliers: card.multipliers,
    baseRewardRate: card.base_reward_rate,
    rewardsCurrency: card.rewards_currency,
    region: card.region,
    annualFee: card.annual_fee,
    sourceUrls: card.source_urls,
    lastFour: card.last_four ?? "",
    needsManual: false,
    alias: null,
    nextAnnualFeeDate: null,
    clientRequestId: card.client_request_id,
  };
}

/**
 * Validate and narrow a render_chart tool result into the local ChartSpec
 * shape. Anything that doesn't pass the structural check is dropped — the
 * backend's RenderChartRequest validator already enforces shape on the way
 * in, so a failure here means the loop returned something we can't trust
 * to render. Returns null for the caller to skip cleanly.
 */
function _toolToChartSpec(call: ChatToolCall): ChartSpec | null {
  const r = call.result;
  if (!r || typeof r !== "object") return null;
  const type = r.type;
  if (
    type !== "line" &&
    type !== "bar" &&
    type !== "stacked_bar" &&
    type !== "donut"
  ) {
    return null;
  }
  if (!Array.isArray(r.x) || r.x.length === 0) return null;
  const x = r.x.filter((v): v is string => typeof v === "string");
  if (x.length !== r.x.length) return null;
  if (!Array.isArray(r.series) || r.series.length === 0) return null;
  const series: ChartSpec["series"] = [];
  for (const s of r.series) {
    if (!s || typeof s !== "object") return null;
    const name = (s as { name?: unknown }).name;
    const data = (s as { data?: unknown }).data;
    if (typeof name !== "string") return null;
    if (!Array.isArray(data) || data.length !== x.length) return null;
    if (!data.every((d) => typeof d === "number" && Number.isFinite(d))) {
      return null;
    }
    series.push({ name, data: data as number[] });
  }
  const title = typeof r.title === "string" ? r.title : "";
  if (!title) return null;
  const y_label = typeof r.y_label === "string" ? r.y_label : undefined;
  return { type, x, series, title, y_label };
}

/**
 * Collapse a server-side chat_messages row into one or more local
 * ChatMessage shapes. v1 rehydrates plain text + parse cards.
 *
 * Day 14b: when `_persist_turn` augments an assistant row's content_blocks
 * with `tameru_proposal` synthetic blocks (one per `propose_transaction` /
 * `propose_card` tool call in the turn), reconstruct those as parse-card
 * messages so a page refresh no longer orphans "here's the parse — tap
 * looks right" prose with no card to tap. The agent's prose becomes the
 * preface on the first parse card, matching `_renderTurn`'s fresh-turn
 * behavior.
 *
 * Confirmation state isn't persisted today — re-tapping "looks right" on
 * a rehydrated transaction proposal is safe via `client_request_id`
 * idempotency on POST /transactions/confirm; for cards, the 409
 * active_card_exists path in `commitCardDraft` flips the card to a
 * committed state with the existing row's id.
 *
 * Candidate lists are still session-scoped and never rehydrated — their
 * `candidateIds` reference the in-session ledger snapshot, which has no
 * meaning on a fresh page mount.
 *
 * Returns [] when the row has no rendering-worthy content (no text and no
 * proposals), so the caller can `.flatMap` cleanly.
 */
function _wireMessageToLocal(m: ChatMessageWire): ChatMessage[] {
  const text = m.content_blocks
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text as string)
    .join("\n\n")
    .trim();

  if (m.role === "user") {
    if (!text) return [];
    return [{ id: newId("user"), role: "user", text }];
  }

  // Assistant row — may carry `tameru_proposal` blocks alongside text.
  // The backend (chat.py `_annotate_committed_proposals`) annotates each
  // block with `committed_id` + `committed_state` (`'active'` | `'deleted'`)
  // when the proposal's client_request_id (transactions) or name (cards)
  // matches a row in the user's ledger/wallet. Rehydrated cards are
  // ALWAYS rendered read-only (`frozen: true`); the committed_state drives
  // the badge (logged. / deleted. / not saved.).
  const proposals = m.content_blocks.filter(
    (b) => b.type === "tameru_proposal",
  ) as Array<{
    type: "tameru_proposal";
    tool_name?: unknown;
    input?: unknown;
    result?: unknown;
    committed_id?: unknown;
    committed_state?: unknown;
    /**
     * Day 15 addition: the matched row's current user-editable fields,
     * stitched on by `_annotate_committed_proposals`. Present only when
     * `committed_id` is also present (i.e., the proposal has been
     * confirmed and a row exists). Drives the rehydrated parse card's
     * displayed values so they match the ledger, not the agent's
     * original suggestion. See `_proposalToDraft` for merge semantics.
     */
    committed_payload?: unknown;
  }>;

  if (proposals.length === 0) {
    if (!text) return [];
    return [{ id: newId("ai"), role: "assistant", kind: "text", text }];
  }

  const out: ChatMessage[] = [];
  let prefaceClaimed = false;

  for (let i = 0; i < proposals.length; i++) {
    const p = proposals[i];
    const synthetic: ChatToolCall = {
      name: typeof p.tool_name === "string" ? p.tool_name : "",
      input: (p.input && typeof p.input === "object"
        ? (p.input as Record<string, unknown>)
        : {}) as Record<string, unknown>,
      result: (p.result && typeof p.result === "object"
        ? (p.result as Record<string, unknown>)
        : {}) as Record<string, unknown>,
    };
    const committedId =
      typeof p.committed_id === "string" ? p.committed_id : undefined;
    const committedState =
      p.committed_state === "active" || p.committed_state === "deleted"
        ? p.committed_state
        : undefined;

    if (synthetic.name === "propose_transaction") {
      const draft = _proposalToDraft(synthetic, p.committed_payload);
      if (!draft) continue;
      out.push({
        id: newId("ai"),
        role: "assistant",
        kind: "parse",
        preface: !prefaceClaimed && text ? text : undefined,
        draft,
        committedTxId: committedId,
        committedState,
        frozen: true,
      });
      prefaceClaimed = true;
    } else if (synthetic.name === "propose_card") {
      const draft = _proposalToCardDraft(synthetic, p.committed_payload);
      if (!draft) continue;
      out.push({
        id: newId("ai"),
        role: "assistant",
        kind: "card-parse",
        preface: !prefaceClaimed && text ? text : undefined,
        draft,
        committedCardId: committedId,
        committedState,
        frozen: true,
      });
      prefaceClaimed = true;
    } else if (synthetic.name === "propose_subscription") {
      const draft = _proposalToSubscriptionDraft(
        synthetic,
        p.committed_payload,
      );
      if (!draft) continue;
      // Subscriptions have three lifecycle states (active/paused/
      // cancelled), not just active/deleted. Validate against the
      // wider enum here.
      const subState =
        p.committed_state === "active" ||
        p.committed_state === "paused" ||
        p.committed_state === "cancelled"
          ? p.committed_state
          : undefined;
      out.push({
        id: newId("ai"),
        role: "assistant",
        kind: "subscription-parse",
        preface: !prefaceClaimed && text ? text : undefined,
        draft,
        committedSubscriptionId: committedId,
        committedState: subState,
        frozen: true,
      });
      prefaceClaimed = true;
    }
  }

  // If the agent emitted prose but no proposal in the row rehydrated
  // successfully (every _proposalTo* helper returned null), still surface
  // the text so the conversation doesn't drop the assistant turn entirely.
  if (out.length === 0 && text) {
    out.push({ id: newId("ai"), role: "assistant", kind: "text", text });
  }

  // If prose existed but was already claimed as a preface, we're done.
  // If prose existed AND wasn't claimed (every proposal pushed without
  // claiming — only happens when out is empty after all skips, handled
  // above), the fallback bubble already covered it.
  return out;
}

/**
 * Test-only handles. The `_wireMessageToLocal` mapper is private to the
 * module (it captures the rehydrate semantics including `committed_payload`
 * precedence — Day 15); exposing it here lets unit tests drive it
 * directly without an HTTP round-trip or a persisted conversation id.
 * Production code must not import `_testing`.
 */
export const _testing = {
  wireMessageToLocal: _wireMessageToLocal,
};
