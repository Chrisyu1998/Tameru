/**
 * chatStore.commitSubscriptionDraft test — Day 19.
 *
 * Covers the chat-side subscription create flow:
 *   - Happy path: `/subscriptions/confirm` returns a row; the parse
 *     card flips to `committedSubscriptionId` set + status reflected
 *     in `committedState`.
 *   - Network error: enqueues under `kind: "subscription"` and marks
 *     the parse card `pendingSync: true`.
 *
 * Mocks `@/lib/subscriptionsApi` to keep the test offline; mocks
 * `@/lib/subscriptions` to assert `addSubscriptionLocal` is called.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/subscriptionsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/subscriptionsApi")>(
    "@/lib/subscriptionsApi",
  );
  return { ...actual, confirmSubscription: vi.fn() };
});

vi.mock("@/lib/subscriptions", async () => {
  const actual = await vi.importActual<typeof import("@/lib/subscriptions")>(
    "@/lib/subscriptions",
  );
  return { ...actual, addSubscriptionLocal: vi.fn() };
});

vi.mock("@/lib/offline_queue", async () => {
  const actual = await vi.importActual<typeof import("@/lib/offline_queue")>(
    "@/lib/offline_queue",
  );
  return { ...actual, enqueue: vi.fn(async () => ({}) as never) };
});

import { confirmSubscription } from "@/lib/subscriptionsApi";
import type { SubscriptionRow } from "@/lib/subscriptionsApi";
import { addSubscriptionLocal } from "@/lib/subscriptions";
import { enqueue } from "@/lib/offline_queue";
import { useAppStore } from "../src/store";
import { chatStore } from "@/lib/chatStore";
import type {
  AssistantSubscriptionParseMessage,
  SubscriptionParseDraft,
} from "@/lib/chat";

const confirmMock = vi.mocked(confirmSubscription);
const addSubMock = vi.mocked(addSubscriptionLocal);
const enqueueMock = vi.mocked(enqueue);

const PARSE_MSG_ID = "sub-parse-1";
const SUB_CRID = "00000000-0000-0000-0000-0000000019cc";

function makeDraft(
  overrides: Partial<SubscriptionParseDraft> = {},
): SubscriptionParseDraft {
  return {
    name: "Netflix",
    amount: "15.99",
    frequency: "monthly",
    startDate: "2026-05-19",
    nextBillingDate: "2026-06-19",
    category: "Streaming",
    cardId: null,
    clientRequestId: SUB_CRID,
    ...overrides,
  };
}

function seedParseMessage(): AssistantSubscriptionParseMessage {
  const msg: AssistantSubscriptionParseMessage = {
    id: PARSE_MSG_ID,
    role: "assistant",
    kind: "subscription-parse",
    draft: makeDraft(),
  };
  chatStore.setMessages([msg]);
  return msg;
}

function subRow(overrides: Partial<SubscriptionRow> = {}): SubscriptionRow {
  return {
    id: "sub-server-1",
    user_id: "user-a",
    card_id: null,
    name: "Netflix",
    amount: "15.99",
    frequency: "monthly",
    start_date: "2026-05-19",
    next_billing_date: "2026-06-19",
    category: "Streaming",
    status: "active",
    client_request_id: SUB_CRID,
    created_at: "2026-05-19T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  chatStore.newChat();
  confirmMock.mockReset();
  addSubMock.mockReset();
  enqueueMock.mockReset();
  useAppStore.setState({
    user: { id: "user-a", email: "a@example.com" },
    jwt: "fake",
    deviceId: "dev-a",
  });
});

afterEach(() => {
  useAppStore.setState({ user: null, jwt: null });
});

describe("chatStore.commitSubscriptionDraft", () => {
  test("happy path → POST fires, addSubscriptionLocal called, parse card flips to active", async () => {
    seedParseMessage();
    confirmMock.mockResolvedValueOnce(subRow());

    await chatStore.commitSubscriptionDraft(PARSE_MSG_ID, makeDraft());

    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(addSubMock).toHaveBeenCalledTimes(1);
    const messages = chatStore.getSnapshot().messages;
    const parse = messages.find(
      (m) => m.role === "assistant" && m.kind === "subscription-parse",
    ) as AssistantSubscriptionParseMessage;
    expect(parse).toBeDefined();
    expect(parse.committedSubscriptionId).toBe("sub-server-1");
    expect(parse.committedState).toBe("active");
    expect(parse.pendingSync).toBe(false);
  });

  test("network error → enqueues under kind=subscription, parse card marked pendingSync", async () => {
    seedParseMessage();
    confirmMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await chatStore.commitSubscriptionDraft(PARSE_MSG_ID, makeDraft());

    expect(enqueueMock).toHaveBeenCalledTimes(1);
    const enqueued = enqueueMock.mock.calls[0][0];
    expect(enqueued.kind).toBe("subscription");
    expect(enqueued.ownerUserId).toBe("user-a");
    const messages = chatStore.getSnapshot().messages;
    const parse = messages.find(
      (m) => m.role === "assistant" && m.kind === "subscription-parse",
    ) as AssistantSubscriptionParseMessage;
    expect(parse.pendingSync).toBe(true);
    expect(parse.committedSubscriptionId).toBeUndefined();
  });

  test("invalid amount string short-circuits before the API call", async () => {
    seedParseMessage();
    await chatStore.commitSubscriptionDraft(
      PARSE_MSG_ID,
      makeDraft({ amount: "not-a-number" }),
    );
    expect(confirmMock).not.toHaveBeenCalled();
  });
});
