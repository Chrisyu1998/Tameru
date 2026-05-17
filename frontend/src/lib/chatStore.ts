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
  type TransactionRowWire,
} from "./transactionsApi";

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
    };

    try {
      const result = await confirmTransaction(body);
      const tx = result.transaction;
      // Optimistic local injection. lib/ledger.ts also refetches on demand
      // via ledger.refresh(); calling it here would re-trip a network round
      // trip that we don't need since `tx` already has the row.
      ledger.addTransaction(tx);
      // Flip the parse card to committed first; append the entry-moment
      // insight bubble after so it visually lands beneath the card.
      const committedMessages = state.messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "parse"
          ? { ...m, draft, committedTxId: tx.id }
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
              text: insight,
            },
          ],
        });
      } else {
        setState({ messages: committedMessages });
      }
    } catch (err) {
      // Surface the actual reason in the console — the inline chat bubble
      // is intentionally vague, but a 422/500/network failure should be
      // diagnosable from devtools without re-running the flow.
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
   * Day 14 cards have no `client_request_id` idempotency (DESIGN.md §8.1 —
   * cards are ≤10/user lifetime; cost of duplicate is a tap to delete). If
   * the user re-taps "looks right" on a rehydrated card proposal that was
   * already committed, the server returns 409 `active_card_exists`; we
   * surface a quiet text bubble so the user knows the card is already in
   * their wallet rather than silently dropping the click.
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

    const body: CardProposal = {
      network: draft.network,
      last_four: draft.lastFour,
      name: draft.name,
      issuer: draft.issuer,
      program: draft.program,
      multipliers: draft.multipliers,
      annual_fee: draft.annualFee,
      source_urls: draft.sourceUrls,
      alias: draft.alias ?? null,
      needs_manual: draft.needsManual,
    };

    try {
      const card = await confirmCard(body);
      const committedMessages = state.messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "card-parse"
          ? { ...m, draft, committedCardId: card.id }
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
            ? { ...m, draft, committedCardId: detail.existing_card_id }
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
      // eslint-disable-next-line no-console
      console.error("commitCardDraft → /cards/confirm failed", err);
      appendAssistantText("couldn't add that card. try again in a moment.");
    }
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
      if (state.conversationId !== payload.conversation_id) {
        writePersistedConvoId(payload.conversation_id);
      }
      if (state.capEngaged) setState({ capEngaged: false });
      setState({
        conversationId: payload.conversation_id,
        streamingText: "",
        lastError: null,
      });
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
 *     `via` attribution chip on the trailing text bubble. If the agent
 *     emitted multiple, the first one wins for the chip — the full list
 *     remains in chat_turn_trace for future Day 16 surfacing.
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
      via: "find_transactions",
    });
  }

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
      via: "calculate_total",
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
      via: _toolToVia(otherToolName),
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
interface TransactionProposalWire {
  merchant: string;
  amount: string | number;
  date: string;
  card_id: string | null;
  category: Category;
  notes: string | null;
  gemini_suggestion: string | null;
  client_request_id: string;
}

function _proposalToDraft(call: ChatToolCall): ParseDraft | null {
  // Backend response_model is TransactionProposal (app/models/transactions.py)
  // and the local Category union now covers every backend value, so we
  // trust the wire shape. Day 10b §1: "if a server row still returns
  // something outside the union it's a real bug, not a cast to paper over."
  const r = call.result as unknown as TransactionProposalWire;
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
  };
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
  annual_fee: string | number | null;
  source_urls: string[];
  alias: string | null;
  needs_manual: boolean;
}

function _proposalToCardDraft(call: ChatToolCall): CardParseDraft | null {
  const r = call.result as unknown as CardProposalWire;
  if (!r || typeof r !== "object") return null;
  if (typeof r.name !== "string" || !r.name) return null;

  // Backend defaults network to "other" and issuer to "other" when the
  // lookup couldn't determine them (see propose_card in app/agent/tools.py).
  // Surface that as null on the local draft so the CardParseCard renders
  // the unresolved "select…" state with a warn ring, matching AddCardStep's
  // posture — silently defaulting both to "other" would let the user save
  // wrong identity metadata and trip the (user_id, issuer, last_four)
  // unique index on the next add of a real card with the same last 4.
  const issuer =
    r.issuer === "other" && r.needs_manual ? null : r.issuer ?? null;
  const network =
    r.network === "other" && r.needs_manual ? null : r.network ?? null;

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
    annualFee,
    sourceUrls: Array.isArray(r.source_urls) ? r.source_urls : [],
    lastFour: typeof r.last_four === "string" ? r.last_four : "",
    needsManual: r.needs_manual === true,
    alias: typeof r.alias === "string" ? r.alias : null,
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
 * Translate a backend tool name into the local `ToolName` union the
 * MessageBubble `via` attribution chip renders. Tools without a local
 * equivalent fall through to undefined (no chip).
 */
function _toolToVia(name: string | undefined): ToolName | undefined {
  // The ToolName union in chat.ts is intentionally narrow (Lovable-era
  // local tool names). Map closest backend equivalents onto it.
  switch (name) {
    case "get_transactions":
      return "find_transactions";
    case "calculate_total":
    case "get_spending_summary":
      return "calculate_total";
    default:
      return undefined;
  }
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
      const draft = _proposalToDraft(synthetic);
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
      const draft = _proposalToCardDraft(synthetic);
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
