/**
 * chatStore.sendReceiptPhoto test — receipt photo → parse card.
 *
 * The camera button (chat.tsx / DesktopComposer.tsx) downscales an image and
 * hands the blob to this store action, which uploads it to POST /receipts/parse
 * and turns the returned proposal into a parse card the user confirms through
 * the normal commitDraft path. We mock `@/lib/receiptsApi` so the test runs
 * offline; the contract:
 *   - success → a user bubble + a `parse` card whose draft carries
 *     source='receipt_photo' and the server's client_request_id; busy clears.
 *   - a request error (ApiError) → a user bubble + an assistant text bubble.
 *   - a malformed proposal (missing fields → null draft) → assistant text.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

vi.mock('@/lib/receiptsApi', async () => {
  const actual = await vi.importActual<typeof import('@/lib/receiptsApi')>(
    '@/lib/receiptsApi',
  );
  return { ...actual, parseReceipt: vi.fn() };
});

import { parseReceipt } from '@/lib/receiptsApi';
import type { TransactionProposalWire } from '@/lib/receiptsApi';
import { chatStore } from '@/lib/chatStore';
import { ApiError } from '@/lib/api';
import type { AssistantParseMessage } from '@/lib/chat';

const parseMock = vi.mocked(parseReceipt);

function makeWire(
  overrides: Partial<TransactionProposalWire> = {},
): TransactionProposalWire {
  return {
    merchant: "Trader Joe's",
    amount: '47.02',
    date: '2026-07-01',
    card_id: null,
    category: 'Groceries',
    notes: null,
    gemini_suggestion: 'Groceries',
    client_request_id: '00000000-0000-0000-0000-0000000000bb',
    source: 'receipt_photo',
    ...overrides,
  };
}

function jpeg(): Blob {
  return new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' });
}

describe('chatStore.sendReceiptPhoto', () => {
  beforeEach(() => {
    chatStore.newChat();
  });

  afterEach(() => {
    parseMock.mockReset();
  });

  test('success appends a user bubble + a receipt_photo parse card', async () => {
    parseMock.mockResolvedValueOnce(makeWire());

    await chatStore.sendReceiptPhoto(jpeg());

    const messages = chatStore.getSnapshot().messages;
    expect(messages).toHaveLength(2);
    expect(messages[0].role).toBe('user');

    const parse = messages[1] as AssistantParseMessage;
    expect(parse.kind).toBe('parse');
    expect(parse.draft.merchant).toBe("Trader Joe's");
    expect(parse.draft.amountCents).toBe(4702);
    expect(parse.draft.source).toBe('receipt_photo');
    expect(parse.draft.clientRequestId).toBe(
      '00000000-0000-0000-0000-0000000000bb',
    );
    expect(chatStore.getSnapshot().busy).toBe(false);
  });

  test('a request error appends an assistant error bubble, not a parse card', async () => {
    parseMock.mockRejectedValueOnce(
      new ApiError(503, { detail: { code: 'provider_error' } }, 'boom'),
    );

    await chatStore.sendReceiptPhoto(jpeg());

    const messages = chatStore.getSnapshot().messages;
    expect(messages).toHaveLength(2);
    expect(messages[0].role).toBe('user');
    expect(messages[1].kind).toBe('text');
    expect(chatStore.getSnapshot().busy).toBe(false);
  });

  test('a malformed proposal (missing merchant) yields an error bubble', async () => {
    // Empty merchant → _wireProposalToDraft returns null (defensive branch).
    parseMock.mockResolvedValueOnce(makeWire({ merchant: '' }));

    await chatStore.sendReceiptPhoto(jpeg());

    const messages = chatStore.getSnapshot().messages;
    expect(messages[messages.length - 1].kind).toBe('text');
    expect(
      messages.some((m) => m.kind === 'parse'),
    ).toBe(false);
  });
});
