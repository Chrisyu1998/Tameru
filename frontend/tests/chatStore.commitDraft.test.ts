/**
 * chatStore.commitDraft test — Day 13.
 *
 * Verifies the insight-bubble plumbing on top of the parse-card commit
 * flow. The contract:
 *   - When /transactions/confirm returns a non-null `insight`, the
 *     chatStore appends exactly one extra message (the insight bubble)
 *     beneath the now-committed parse card.
 *   - When `insight` is null, no extra message appears — the parse card
 *     simply flips to its committed state.
 *
 * We mock `@/lib/transactionsApi` so the test runs offline, and mock
 * `@/lib/ledger`'s `addTransaction` because chatStore optimistically
 * splices the new row in there.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

vi.mock('@/lib/transactionsApi', async () => {
  const actual = await vi.importActual<typeof import('@/lib/transactionsApi')>(
    '@/lib/transactionsApi',
  );
  return {
    ...actual,
    confirmTransaction: vi.fn(),
  };
});

vi.mock('@/lib/ledger', async () => {
  const actual = await vi.importActual<typeof import('@/lib/ledger')>(
    '@/lib/ledger',
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

import { confirmTransaction } from '@/lib/transactionsApi';
import type { ConfirmTransactionResult } from '@/lib/transactionsApi';
import { chatStore } from '@/lib/chatStore';
import type {
  AssistantParseMessage,
  InsightSeverity,
  ParseDraft,
} from '@/lib/chat';
import type { Transaction } from '@/lib/fixtures';

const confirmMock = vi.mocked(confirmTransaction);

const PARSE_MSG_ID = 'parse-1';

function makeDraft(overrides: Partial<ParseDraft> = {}): ParseDraft {
  return {
    merchant: 'Lupa',
    amountCents: 4200,
    date: '2026-05-13',
    cardId: '',
    category: 'Dining',
    confidence: {
      merchant: 0.95,
      amount: 0.95,
      date: 0.95,
      card: 0.95,
      category: 0.95,
    },
    clientRequestId: '00000000-0000-0000-0000-00000000aaaa',
    notes: null,
    geminiSuggestion: 'Dining',
    ...overrides,
  };
}

function seedParseMessage(): AssistantParseMessage {
  const msg: AssistantParseMessage = {
    id: PARSE_MSG_ID,
    role: 'assistant',
    kind: 'parse',
    draft: makeDraft(),
  };
  chatStore.setMessages([msg]);
  return msg;
}

function makeTx(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-server-1',
    merchant: 'Lupa',
    amountCents: 4200,
    date: '2026-05-13',
    cardId: '',
    category: 'Dining',
    autoLogged: false,
    ...overrides,
  };
}

function mockConfirm(
  insight: { text: string; severity: InsightSeverity } | null,
): void {
  const result: ConfirmTransactionResult = { transaction: makeTx(), insight };
  confirmMock.mockResolvedValueOnce(result);
}

describe('chatStore.commitDraft — Day 13 insight bubble', () => {
  beforeEach(() => {
    chatStore.newChat();
  });

  afterEach(() => {
    confirmMock.mockReset();
  });

  test('non-null insight appends exactly one extra assistant message', async () => {
    seedParseMessage();
    mockConfirm({
      text: 'highest single dining spend this month.',
      severity: 'calm',
    });

    await chatStore.commitDraft(PARSE_MSG_ID, makeDraft());

    const messages = chatStore.getSnapshot().messages;
    // Exactly two messages: the committed parse card + the insight bubble.
    expect(messages).toHaveLength(2);
    const [committed, insight] = messages;
    expect(committed.kind).toBe('parse');
    expect(insight.role).toBe('assistant');
    expect(insight.kind).toBe('insight');
    if (insight.kind === 'insight') {
      expect(insight.text).toBe('highest single dining spend this month.');
      expect(insight.severity).toBe('calm');
    }
  });

  test('insight severity flows through commit to the bubble message', async () => {
    seedParseMessage();
    mockConfirm({
      text: 'on pace for about $160 over your monthly dining average.',
      severity: 'alert',
    });

    await chatStore.commitDraft(PARSE_MSG_ID, makeDraft());

    const messages = chatStore.getSnapshot().messages;
    const insight = messages[messages.length - 1];
    expect(insight.kind).toBe('insight');
    if (insight.kind === 'insight') {
      expect(insight.severity).toBe('alert');
      expect(insight.text).toBe(
        'on pace for about $160 over your monthly dining average.',
      );
    }
  });

  test('null insight leaves the message count unchanged after commit', async () => {
    seedParseMessage();
    mockConfirm(null);

    await chatStore.commitDraft(PARSE_MSG_ID, makeDraft());

    const messages = chatStore.getSnapshot().messages;
    expect(messages).toHaveLength(1);
    expect(messages[0].kind).toBe('parse');
  });

  test('parse card flips to committed before insight lands beneath it', async () => {
    seedParseMessage();
    mockConfirm({
      text: '4th dining transaction this week — you usually have 2.',
      severity: 'calm',
    });

    await chatStore.commitDraft(PARSE_MSG_ID, makeDraft());

    const messages = chatStore.getSnapshot().messages;
    const parse = messages[0] as AssistantParseMessage;
    expect(parse.committedTxId).toBe('tx-server-1');
    // Insight is the LAST message so it visually lands below the card.
    expect(messages[messages.length - 1].kind).toBe('insight');
  });
});
