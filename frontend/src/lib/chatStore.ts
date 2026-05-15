/**
 * Shared chat session store. Used by both the mobile /chat route and the
 * desktop right-side ChatDrawer so they reflect a single conversation.
 *
 * Talks to the real backend (POST /chat/turn — app/routes/chat.py) and
 * commits proposals through POST /transactions/confirm. Conversation_id is
 * minted server-side on the first turn and replayed on every subsequent
 * one so the agent loop sees the last 5 turns of history (DESIGN.md §7.2.1).
 *
 * No streaming yet — Day 12 swaps the one-shot POST for SSE. Until then,
 * the UI shows a `busy` flag while the model runs (typically 4-6s per turn).
 */

import { useEffect, useState } from "react";
import {
  isDailyCapEngaged,
  newId,
  type ChatMessage,
  type ParseDraft,
  type ToolName,
} from "./chat";
import { ledger } from "./ledger";
import type { Category } from "./categories";
import type { Transaction } from "./fixtures";
import {
  postChatTurn,
  toChatTurnError,
  type ChatToolCall,
  type ChatTurnResponse,
} from "./chatApi";
import {
  confirmTransaction,
  fromWire,
  sanitizeCardId,
  type ConfirmTransactionBody,
  type TransactionRowWire,
} from "./transactionsApi";

type Listener = () => void;

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
}

let state: ChatState = {
  messages: [],
  drawerOpen: false,
  drawerExpanded: false,
  conversationId: null,
  busy: false,
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
   * Send one turn. Appends the user bubble immediately, then either appends
   * assistant message(s) on success or a single error bubble on failure.
   * Re-entry is guarded by `busy` — a second send before the first resolves
   * is a no-op.
   */
  async send(raw: string) {
    const text = raw.trim();
    if (!text) return;
    if (state.busy) return;
    if (isDailyCapEngaged()) return;

    appendMessages({ id: newId("user"), role: "user", text });
    setState({ busy: true });

    // Offline check is cheap and avoids a confusing 0-status error path.
    // The PWA Service Worker doesn't proxy /chat (see vite.config.ts
    // navigateFallbackDenylist) so the browser will surface the failure
    // straight to fetch.
    const online = typeof navigator === "undefined" ? true : navigator.onLine;
    if (!online) {
      appendAssistantText(
        "you're offline right now. i'll send this once you're back on a connection.",
      );
      setState({ busy: false });
      return;
    }

    try {
      const res = await postChatTurn(text, state.conversationId);
      setState({ conversationId: res.conversation_id });
      _renderTurn(res);
    } catch (err) {
      const e = toChatTurnError(err);
      if (e.code === "UCAP_EXCEEDED") {
        appendAssistantText(
          "we've hit today's chat budget. try again tomorrow.",
        );
      } else if (e.code === "PROVIDER_RATE_LIMITED") {
        appendAssistantText(
          "our ai is having a moment. try again in a few minutes.",
        );
      } else if (e.code === "LOOP_LIMIT") {
        appendAssistantText("something went wrong on our end. try rephrasing?");
      } else {
        appendAssistantText("couldn't reach the chat. check your connection?");
      }
    } finally {
      setState({ busy: false });
    }
  },

  setMessages(messages: ChatMessage[]) {
    setState({ messages });
  },

  /**
   * Reset the visible thread and the server-side conversation pointer.
   * Next turn starts a fresh conversation (server mints a new UUID).
   */
  newChat() {
    setState({ messages: [], conversationId: null });
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
      const tx = await confirmTransaction(body);
      // Optimistic local injection. lib/ledger.ts also refetches on demand
      // via ledger.refresh(); calling it here would re-trip a network round
      // trip that we don't need since `tx` already has the row.
      ledger.addTransaction(tx);
      setState({
        messages: state.messages.map((m) =>
          m.id === msgId && m.role === "assistant" && m.kind === "parse"
            ? { ...m, draft, committedTxId: tx.id }
            : m,
        ),
      });
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
  const getTransactionsCalls = res.tool_calls.filter(
    (tc) => tc.name === "get_transactions",
  );
  // Pick a non-propose, non-get_transactions tool for the attribution chip on
  // the trailing text bubble (the dedicated renderers already attribute
  // themselves). First wins.
  const otherToolName = res.tool_calls.find(
    (tc) =>
      tc.name !== "propose_transaction" && tc.name !== "get_transactions",
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

  // Render get_transactions as a candidate list — the agent typically uses
  // it for "show me / find / delete the X" intents. We injects the returned
  // rows into the ledger so CandidateCards' findTx(id) lookup resolves and
  // the dashboard learns about any older rows the initial /transactions
  // fetch hadn't surfaced.
  //
  // Default intent is "edit" — tapping a candidate opens the edit sheet,
  // which already has a delete button, so this also satisfies the "delete
  // the X" path with one extra tap. Inferring delete-intent from
  // assistant_text would be cheap but error-prone; punt to v1+.
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
      intent: "edit",
      via: "find_transactions",
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
function _proposalToDraft(call: ChatToolCall): ParseDraft | null {
  const r = call.result;
  const merchant = typeof r.merchant === "string" ? r.merchant : null;
  const amountRaw = r.amount;
  const date = typeof r.date === "string" ? r.date : null;
  const category = typeof r.category === "string" ? (r.category as Category) : null;
  const clientRequestId =
    typeof r.client_request_id === "string" ? r.client_request_id : null;
  if (!merchant || !date || !category || !clientRequestId) return null;

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
