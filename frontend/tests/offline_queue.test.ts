/**
 * offline_queue test — Day 15.
 *
 * Covers the Day 15 spec's queue+drain semantics:
 *   - enqueue + drain on the FIFO order.
 *   - 2xx success patches the in-memory parse card to `logged.` with the
 *     response payload (the "edit-then-queue" survives — user-edited
 *     amount is what gets POSTed and what the rehydrated card displays).
 *   - 409 active_card_exists silently dequeues the card.
 *   - 422 dequeues AND surfaces an editable parse card + error bubble.
 *   - 5xx leaves the entry in queue for the next online event.
 *   - Cross-user safety: user A's entry doesn't drain under user B's
 *     session.
 *   - Persist across reload: an entry written before reload is still
 *     readable from IDB after a simulated reload.
 *
 * IDB is provided by `fake-indexeddb/auto` (loaded in `tests/setup.ts`);
 * the queue uses the `idb` wrapper, which sees the polyfill transparently.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/transactionsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/transactionsApi")>(
      "@/lib/transactionsApi",
    );
  return { ...actual, confirmTransaction: vi.fn() };
});

vi.mock("@/lib/cardsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/cardsApi")>("@/lib/cardsApi");
  return { ...actual, confirmCard: vi.fn() };
});

vi.mock("@/lib/subscriptionsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/subscriptionsApi")>(
      "@/lib/subscriptionsApi",
    );
  return { ...actual, confirmSubscription: vi.fn() };
});

vi.mock("@/lib/subscriptions", async () => {
  const actual = await vi.importActual<typeof import("@/lib/subscriptions")>(
    "@/lib/subscriptions",
  );
  return {
    ...actual,
    addSubscriptionLocal: vi.fn(),
  };
});

vi.mock("@/lib/ledger", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ledger")>(
    "@/lib/ledger",
  );
  return {
    ...actual,
    ledger: {
      ...actual.ledger,
      addTransaction: vi.fn((tx) => tx),
      refresh: vi.fn(async () => {}),
    },
  };
});

import { confirmTransaction } from "@/lib/transactionsApi";
import type {
  ConfirmTransactionBody,
  ConfirmTransactionResult,
} from "@/lib/transactionsApi";
import { confirmCard } from "@/lib/cardsApi";
import type { CardProposal, CardRow } from "@/lib/cardsApi";
import { confirmSubscription } from "@/lib/subscriptionsApi";
import type {
  SubscriptionProposal,
  SubscriptionRow,
} from "@/lib/subscriptionsApi";
import { addSubscriptionLocal } from "@/lib/subscriptions";
import { useAppStore } from "../src/store";
import { chatStore } from "@/lib/chatStore";
import {
  enqueue,
  drainQueue,
  listForUser,
  getPendingCount,
  _clearAll,
  _resetForTests,
} from "@/lib/offline_queue";
import { ApiError } from "@/lib/api";
import type {
  AssistantParseMessage,
  AssistantCardParseMessage,
  ParseDraft,
  CardParseDraft,
} from "@/lib/chat";
import type { Transaction } from "@/lib/fixtures";

const confirmTxMock = vi.mocked(confirmTransaction);
const confirmCardMock = vi.mocked(confirmCard);
const confirmSubMock = vi.mocked(confirmSubscription);
const addSubMock = vi.mocked(addSubscriptionLocal);

const USER_A = { id: "user-a-uuid", email: "a@example.com" };
const USER_B = { id: "user-b-uuid", email: "b@example.com" };

const CRID_1 = "00000000-0000-0000-0000-0000000000a1";
const CRID_2 = "00000000-0000-0000-0000-0000000000a2";

function signIn(user: { id: string; email: string }): void {
  useAppStore.setState({ user, jwt: "fake-jwt", deviceId: "device-a" });
}

function signOut(): void {
  useAppStore.setState({ user: null, jwt: null });
}

function txBody(
  crid: string,
  overrides: Partial<ConfirmTransactionBody> = {},
): ConfirmTransactionBody {
  return {
    merchant: "Lupa",
    amount: "40.00",
    date: "2026-05-13",
    card_id: null,
    category: "Dining",
    notes: null,
    gemini_suggestion: null,
    client_request_id: crid,
    ...overrides,
  };
}

function txDraft(crid: string, overrides: Partial<ParseDraft> = {}): ParseDraft {
  return {
    merchant: "Lupa",
    amountCents: 4000,
    date: "2026-05-13",
    cardId: "",
    category: "Dining",
    confidence: {
      merchant: 0.95,
      amount: 0.95,
      date: 0.95,
      card: 0.95,
      category: 0.95,
    },
    clientRequestId: crid,
    notes: null,
    geminiSuggestion: "Dining",
    ...overrides,
  };
}

function txRow(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "tx-server-1",
    merchant: "Lupa",
    amountCents: 4000,
    date: "2026-05-13",
    cardId: "",
    category: "Dining",
    autoLogged: false,
    ...overrides,
  };
}

const CARD_CRID = "00000000-0000-0000-0000-0000000000c1";

function cardProposal(overrides: Partial<CardProposal> = {}): CardProposal {
  return {
    network: "amex",
    last_four: "1234",
    name: "Amex Gold",
    issuer: "amex",
    program: "MR",
    multipliers: { Dining: 4 },
    annual_fee: "250",
    source_urls: ["https://nerdwallet.com/amex-gold"],
    alias: null,
    needs_manual: false,
    client_request_id: CARD_CRID,
    ...overrides,
  };
}

function cardRow(overrides: Partial<CardRow> = {}): CardRow {
  return {
    id: "card-server-1",
    user_id: USER_A.id,
    name: "Amex Gold",
    issuer: "amex",
    network: "amex",
    program: "MR",
    multipliers: { Dining: 4 },
    annual_fee: "250",
    last_four: "1234",
    color: null,
    source_urls: ["https://nerdwallet.com/amex-gold"],
    status: "active",
    deleted_at: null,
    created_at: "2026-05-13T00:00:00Z",
    client_request_id: CARD_CRID,
    ...overrides,
  };
}

function seedParseMessage(
  msgId: string,
  draft: ParseDraft,
): AssistantParseMessage {
  const msg: AssistantParseMessage = {
    id: msgId,
    role: "assistant",
    kind: "parse",
    draft,
  };
  chatStore.setMessages([...chatStore.getSnapshot().messages, msg]);
  return msg;
}

function seedCardMessage(
  msgId: string,
  draft: CardParseDraft,
): AssistantCardParseMessage {
  const msg: AssistantCardParseMessage = {
    id: msgId,
    role: "assistant",
    kind: "card-parse",
    draft,
  };
  chatStore.setMessages([...chatStore.getSnapshot().messages, msg]);
  return msg;
}

beforeEach(async () => {
  // Wipe persisted IDB data; tolerate the very-first-run case where the
  // DB doesn't exist yet.
  try {
    await _clearAll();
  } catch {
    /* first run — store hasn't been created yet */
  }
  await _resetForTests();
  chatStore.newChat();
  confirmTxMock.mockReset();
  confirmCardMock.mockReset();
  confirmSubMock.mockReset();
  addSubMock.mockReset();
  signIn(USER_A);
});

afterEach(async () => {
  await _resetForTests();
  signOut();
});

describe("offline_queue — enqueue + drain happy path", () => {
  test("queue → drain → POST fires once → queue empties", async () => {
    confirmTxMock.mockResolvedValueOnce({
      transaction: txRow(),
      insight: null,
    } satisfies ConfirmTransactionResult);

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });
    expect(getPendingCount()).toBe(1);

    await drainQueue();

    expect(confirmTxMock).toHaveBeenCalledTimes(1);
    expect(await listForUser(USER_A.id)).toHaveLength(0);
    expect(getPendingCount()).toBe(0);
  });

  test("edit-then-queue: drain patches in-memory parse card with edited amount", async () => {
    // Spec scenario: user edits amount from $40 to $42 → goes offline →
    // taps "looks right" → reconnect → drain → ParseCard flips to
    // `logged.` with $42, not the original $40, without a page reload.
    const msgId = "parse-1";
    seedParseMessage(msgId, txDraft(CRID_1, { amountCents: 4000 })); // displayed $40
    const editedBody = txBody(CRID_1, { amount: "42.00" }); // queued at $42
    confirmTxMock.mockResolvedValueOnce({
      transaction: txRow({ amountCents: 4200 }), // server returns $42
      insight: "highest single dining spend this month.",
    });

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: editedBody,
      messageId: msgId,
    });
    await drainQueue();

    const messages = chatStore.getSnapshot().messages;
    const parse = messages.find(
      (m) => m.role === "assistant" && m.kind === "parse",
    ) as AssistantParseMessage;
    expect(parse).toBeDefined();
    expect(parse.committedTxId).toBe("tx-server-1");
    expect(parse.committedState).toBe("active");
    expect(parse.pendingSync).toBe(false);
    expect(parse.draft.amountCents).toBe(4200); // edited value, not 4000

    // Insight bubble lands beneath the patched card.
    const insight = messages.find(
      (m) => m.role === "assistant" && m.kind === "insight",
    );
    expect(insight).toBeDefined();
  });

  test("edit-then-queue (card): drain patches in-memory card with edited multipliers", async () => {
    // Symmetric to the transaction edit-then-queue test, with the card-
    // specific match key: cards have no `client_request_id`, so the
    // drain matches the in-memory message by `messageId`. The proposal
    // multipliers were `{Dining: 3}` (lookup default); the user edited
    // to `{Dining: 4}` on the parse card before tapping "looks right";
    // the server commits the row at 4x. The in-memory card must flip
    // to `added.` showing the edited 4x, not the proposal's 3x.
    const msgId = "card-edit-1";
    seedCardMessage(msgId, {
      name: "Amex Gold",
      issuer: "amex",
      network: "amex",
      program: "MR",
      multipliers: { Dining: 3 }, // displayed in the parse card
      annualFee: "250",
      sourceUrls: [],
      lastFour: "1234",
      needsManual: false,
      alias: null,
    });
    // The queued body carries the user's edited 4x.
    const editedProposal = cardProposal({ multipliers: { Dining: 4 } });
    confirmCardMock.mockResolvedValueOnce(
      cardRow({ multipliers: { Dining: 4 } }),
    );

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "card",
      payload: editedProposal,
      messageId: msgId,
    });
    await drainQueue();

    const messages = chatStore.getSnapshot().messages;
    const card = messages.find(
      (m) => m.role === "assistant" && m.kind === "card-parse",
    ) as AssistantCardParseMessage;
    expect(card).toBeDefined();
    expect(card.committedCardId).toBe("card-server-1");
    expect(card.committedState).toBe("active");
    expect(card.pendingSync).toBe(false);
    // The displayed draft now reflects the response payload — the
    // server's 4x, not the parse card's original 3x.
    expect(card.draft.multipliers).toEqual({ Dining: 4 });
    expect(card.draft.lastFour).toBe("1234");
    expect(await listForUser(USER_A.id)).toHaveLength(0);
  });

  test("rehydrate race (card, crid match): drain patches via client_request_id when messageId is stale", async () => {
    // Codex review case: user queues a card offline → closes the tab →
    // reopens. On reopen, `_wireMessageToLocal` mints a *new* React id
    // for the rehydrated parse card; the queue entry's persisted
    // `messageId` no longer points anywhere. The rehydrated draft DOES
    // carry `clientRequestId` (from `result.client_request_id` or
    // `committed_payload.client_request_id` on the persisted block), so
    // the drain's crid match lands cleanly.
    const staleId = "msg-pre-reload-id";
    const freshId = "msg-post-rehydrate-id";
    seedCardMessage(freshId, {
      name: "Amex Gold",
      issuer: "amex",
      network: "amex",
      program: "MR",
      multipliers: { Dining: 4 },
      annualFee: "250",
      sourceUrls: [],
      lastFour: "1234",
      needsManual: false,
      alias: null,
      clientRequestId: CARD_CRID, // rehydrate-stable join key
    });
    chatStore.setMessages(
      chatStore.getSnapshot().messages.map((m) =>
        m.id === freshId && m.role === "assistant" && m.kind === "card-parse"
          ? { ...m, frozen: true }
          : m,
      ),
    );
    confirmCardMock.mockResolvedValueOnce(cardRow());

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "card",
      payload: cardProposal(),
      messageId: staleId, // points nowhere post-reload
    });
    await drainQueue();

    const card = chatStore
      .getSnapshot()
      .messages.find(
        (m) => m.role === "assistant" && m.kind === "card-parse",
      ) as AssistantCardParseMessage;
    expect(card.committedCardId).toBe("card-server-1");
    expect(card.committedState).toBe("active");
    expect(card.frozen).toBe(false);
    expect(card.pendingSync).toBe(false);
    expect(await listForUser(USER_A.id)).toHaveLength(0);
  });

  test("two same-name cards: drain matches the right one via client_request_id", async () => {
    // The original Codex bug, exercised end-to-end. Two uncommitted
    // card-parse messages exist in the chat thread, both named
    // "Amex Gold" (different last_four, different crids). Drain the
    // queued one (last_four=5678, crid=B) → ONLY the matching message
    // flips to `added.`; the other (crid=A) stays uncommitted.
    const CRID_A = "00000000-0000-0000-0000-0000000000aa";
    const CRID_B = "00000000-0000-0000-0000-0000000000bb";
    seedCardMessage("card-msg-A", {
      name: "Amex Gold",
      issuer: "amex",
      network: "amex",
      program: "MR",
      multipliers: { Dining: 4 },
      annualFee: "250",
      sourceUrls: [],
      lastFour: "1234",
      needsManual: false,
      alias: null,
      clientRequestId: CRID_A,
    });
    seedCardMessage("card-msg-B", {
      name: "Amex Gold",
      issuer: "amex",
      network: "amex",
      program: "MR",
      multipliers: { Dining: 4 },
      annualFee: "250",
      sourceUrls: [],
      lastFour: "5678",
      needsManual: false,
      alias: null,
      clientRequestId: CRID_B,
    });
    confirmCardMock.mockResolvedValueOnce(
      cardRow({
        id: "card-server-B",
        last_four: "5678",
        client_request_id: CRID_B,
      }),
    );

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "card",
      payload: cardProposal({
        client_request_id: CRID_B,
        last_four: "5678",
      }),
      messageId: "card-msg-B", // would also match by id, but crid wins first
    });
    await drainQueue();

    const messages = chatStore.getSnapshot().messages;
    const cardA = messages.find(
      (m) =>
        m.role === "assistant" &&
        m.kind === "card-parse" &&
        m.id === "card-msg-A",
    ) as AssistantCardParseMessage;
    const cardB = messages.find(
      (m) =>
        m.role === "assistant" &&
        m.kind === "card-parse" &&
        m.id === "card-msg-B",
    ) as AssistantCardParseMessage;
    expect(cardB.committedCardId).toBe("card-server-B");
    expect(cardB.draft.lastFour).toBe("5678");
    expect(cardA.committedCardId).toBeUndefined(); // untouched
    expect(cardA.draft.lastFour).toBe("1234");
  });

  test("FIFO: two entries drain in queued order", async () => {
    confirmTxMock.mockImplementation(async (body) => ({
      transaction: txRow({ id: `tx-${body.client_request_id}` }),
      insight: null,
    }));

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });
    // Force a non-trivial queuedAt gap to avoid same-ms ordering ambiguity.
    await new Promise((r) => setTimeout(r, 5));
    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_2),
    });

    await drainQueue();

    const calls = confirmTxMock.mock.calls.map(
      (c) => (c[0] as ConfirmTransactionBody).client_request_id,
    );
    expect(calls).toEqual([CRID_1, CRID_2]);
  });
});

describe("offline_queue — error paths", () => {
  test("5xx leaves the entry in the queue for the next online event", async () => {
    confirmTxMock.mockRejectedValueOnce(
      new ApiError(503, { detail: "Service Unavailable" }, "API 503"),
    );

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });
    await drainQueue();

    expect(await listForUser(USER_A.id)).toHaveLength(1);
    expect(getPendingCount()).toBe(1);
  });

  test("network error (non-ApiError) leaves the entry in the queue", async () => {
    confirmTxMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });
    await drainQueue();

    expect(await listForUser(USER_A.id)).toHaveLength(1);
  });

  test("422 dequeues the entry AND re-opens the parse card with a text bubble", async () => {
    const msgId = "parse-perm-failure";
    seedParseMessage(msgId, txDraft(CRID_1));
    // Pre-mark the message as pendingSync so we can assert it clears.
    chatStore.setMessages(
      chatStore.getSnapshot().messages.map((m) =>
        m.id === msgId && m.role === "assistant" && m.kind === "parse"
          ? { ...m, pendingSync: true }
          : m,
      ),
    );
    confirmTxMock.mockRejectedValueOnce(
      new ApiError(
        422,
        { detail: [{ msg: "amount must be positive" }] },
        "API 422",
      ),
    );

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
      messageId: msgId,
    });
    await drainQueue();

    expect(await listForUser(USER_A.id)).toHaveLength(0);
    const messages = chatStore.getSnapshot().messages;
    const parse = messages.find(
      (m) => m.role === "assistant" && m.kind === "parse",
    ) as AssistantParseMessage;
    expect(parse.pendingSync).toBe(false);
    expect(parse.committedTxId).toBeUndefined();
    // A "couldn't sync" text bubble landed below.
    const lastMsg = messages[messages.length - 1];
    expect(lastMsg.role).toBe("assistant");
    expect(lastMsg.kind).toBe("text");
  });

  test("409 active_card_exists silently dequeues the queued card", async () => {
    const msgId = "card-1";
    seedCardMessage(msgId, {
      name: "Amex Gold",
      issuer: "amex",
      network: "amex",
      program: "MR",
      multipliers: { Dining: 4 },
      annualFee: "250",
      sourceUrls: [],
      lastFour: "1234",
      needsManual: false,
      alias: null,
    });
    confirmCardMock.mockRejectedValueOnce(
      new ApiError(
        409,
        {
          detail: {
            code: "active_card_exists",
            message: "already exists",
            existing_card_id: "card-existing-1",
            existing_card_name: "Amex Gold",
            existing_card_last_four: "1234",
          },
        },
        "API 409",
      ),
    );

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "card",
      payload: cardProposal(),
      messageId: msgId,
    });
    await drainQueue();

    expect(await listForUser(USER_A.id)).toHaveLength(0);
    const messages = chatStore.getSnapshot().messages;
    const card = messages.find(
      (m) => m.role === "assistant" && m.kind === "card-parse",
    ) as AssistantCardParseMessage;
    expect(card.committedCardId).toBe("card-existing-1");
    expect(card.pendingSync).toBe(false);
    // No "you already have this card" text bubble — that's the live
    // commitCardDraft 409 path, not the drain path.
    const textBubbles = messages.filter(
      (m) => m.role === "assistant" && m.kind === "text",
    );
    expect(textBubbles).toHaveLength(0);
  });
});

describe("offline_queue — cross-user safety", () => {
  test("user A's entry does NOT drain while user B is signed in", async () => {
    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });
    signIn(USER_B);

    await drainQueue();

    expect(confirmTxMock).not.toHaveBeenCalled();
    // The entry survived under A's owner id.
    expect(await listForUser(USER_A.id)).toHaveLength(1);
    expect(await listForUser(USER_B.id)).toHaveLength(0);

    // Sign A back in → drain succeeds.
    confirmTxMock.mockResolvedValueOnce({
      transaction: txRow(),
      insight: null,
    });
    signIn(USER_A);
    await drainQueue();
    expect(confirmTxMock).toHaveBeenCalledTimes(1);
    expect(await listForUser(USER_A.id)).toHaveLength(0);
  });

  test("drain is a no-op while signed out", async () => {
    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });
    signOut();

    await drainQueue();

    expect(confirmTxMock).not.toHaveBeenCalled();
    expect(await listForUser(USER_A.id)).toHaveLength(1);
  });
});

describe("offline_queue — persistence", () => {
  test("entry survives a simulated reload (IDB property, not React state)", async () => {
    await enqueue({
      ownerUserId: USER_A.id,
      kind: "transaction",
      payload: txBody(CRID_1),
    });

    // Simulate a reload: drop the cached DB handle + in-process state.
    await _resetForTests();

    // The persisted entry is still readable from IDB on a fresh handle.
    const entries = await listForUser(USER_A.id);
    expect(entries).toHaveLength(1);
    expect(entries[0].payload.client_request_id).toBe(CRID_1);
  });
});

/* ─── Day 19 — subscription drain branch ─────────────────────────── */

const SUB_CRID = "00000000-0000-0000-0000-0000000000d1";

function subProposal(
  overrides: Partial<SubscriptionProposal> = {},
): SubscriptionProposal {
  return {
    name: "Netflix",
    amount: "15.99",
    frequency: "monthly",
    start_date: "2026-05-18",
    next_billing_date: "2026-06-18",
    category: "Streaming",
    card_id: null,
    client_request_id: SUB_CRID,
    ...overrides,
  };
}

function subRow(overrides: Partial<SubscriptionRow> = {}): SubscriptionRow {
  return {
    id: "sub-server-1",
    user_id: USER_A.id,
    card_id: null,
    name: "Netflix",
    amount: "15.99",
    frequency: "monthly",
    start_date: "2026-05-18",
    next_billing_date: "2026-06-18",
    category: "Streaming",
    status: "active",
    client_request_id: SUB_CRID,
    created_at: "2026-05-18T00:00:00Z",
    ...overrides,
  };
}

describe("offline_queue — subscription kind (Day 19)", () => {
  test("queued subscription confirm drains, POST fires once, queue empties, store updates", async () => {
    confirmSubMock.mockResolvedValueOnce(subRow());

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "subscription",
      payload: subProposal(),
    });
    expect(getPendingCount()).toBe(1);

    await drainQueue();

    expect(confirmSubMock).toHaveBeenCalledTimes(1);
    // Server-returned row lands in the subscriptions store via the
    // chatStore drain hook.
    expect(addSubMock).toHaveBeenCalledTimes(1);
    expect(addSubMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: "sub-server-1" }),
    );
    expect(await listForUser(USER_A.id)).toHaveLength(0);
    expect(getPendingCount()).toBe(0);
  });

  test("same-crid replay is idempotent at the API layer (server returns existing row)", async () => {
    // Day 19 spec: a same-client_request_id replay returns the existing
    // row with 2xx. The drain treats it identically to a fresh commit —
    // dequeue, patch the store. There's no subscription analog of the
    // card 409 path because subscriptions use crid for dedup, not a
    // natural key.
    confirmSubMock.mockResolvedValueOnce(subRow());

    await enqueue({
      ownerUserId: USER_A.id,
      kind: "subscription",
      payload: subProposal(),
    });
    await drainQueue();

    // Replay the same crid in a second enqueue/drain cycle.
    confirmSubMock.mockResolvedValueOnce(subRow());
    await enqueue({
      ownerUserId: USER_A.id,
      kind: "subscription",
      payload: subProposal(),
    });
    await drainQueue();

    expect(confirmSubMock).toHaveBeenCalledTimes(2);
    // Both calls used the same client_request_id.
    const calls = confirmSubMock.mock.calls.map(
      (c) => (c[0] as SubscriptionProposal).client_request_id,
    );
    expect(calls).toEqual([SUB_CRID, SUB_CRID]);
    expect(await listForUser(USER_A.id)).toHaveLength(0);
  });
});
