/**
 * ParseCard test — Day 10b §8.
 *
 * Covers:
 *   - rendering for each parse-card "kind" via local fixtures
 *   - "looks right" invokes the onConfirm callback with the draft (the
 *     surface that flows into chatStore.commitDraft → /transactions/confirm)
 *   - "let me fix it" preserves unedited fields (it just opens the sheet —
 *     no mutation should happen on the draft from a fix tap)
 */

import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ParseCard } from '@/components/chat/ParseCard';
import type { ParseDraft } from '@/lib/chat';

function makeDraft(overrides: Partial<ParseDraft> = {}): ParseDraft {
  return {
    merchant: 'Blue Bottle',
    amountCents: 550,
    date: '2026-05-13',
    cardId: '',
    category: 'Coffee Shops',
    confidence: {
      merchant: 0.95,
      amount: 0.95,
      date: 0.95,
      card: 0.95,
      category: 0.95,
    },
    clientRequestId: '00000000-0000-0000-0000-000000000001',
    notes: null,
    geminiSuggestion: 'Coffee Shops',
    ...overrides,
  };
}

describe('ParseCard', () => {
  test('renders merchant, amount, category, and confirm/fix buttons', () => {
    render(
      <ParseCard
        preface="got it. does this look right?"
        draft={makeDraft()}
        committed={false}
        onConfirm={() => {}}
        onFix={() => {}}
      />,
    );

    expect(
      screen.getByText('got it. does this look right?'),
    ).toBeInTheDocument();
    expect(screen.getByText('Blue Bottle')).toBeInTheDocument();
    expect(screen.getByText('$5.50')).toBeInTheDocument();
    expect(screen.getByText('Coffee Shops')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /looks right/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /let me fix it/i }),
    ).toBeInTheDocument();
  });

  test('"looks right" calls onConfirm with the draft', async () => {
    const onConfirm = vi.fn();
    const onFix = vi.fn();
    const draft = makeDraft();
    render(
      <ParseCard
        draft={draft}
        committed={false}
        onConfirm={onConfirm}
        onFix={onFix}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /looks right/i }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onFix).not.toHaveBeenCalled();
    // Draft should round-trip with the same wire-payload triple that the
    // chat store needs for /transactions/confirm idempotency.
    const arg = onConfirm.mock.calls[0][0] as ParseDraft;
    expect(arg.merchant).toBe(draft.merchant);
    expect(arg.amountCents).toBe(draft.amountCents);
    expect(arg.date).toBe(draft.date);
    expect(arg.category).toBe(draft.category);
    expect(arg.clientRequestId).toBe(draft.clientRequestId);
  });

  test('"let me fix it" leaves all draft fields untouched in onConfirm later', async () => {
    const onConfirm = vi.fn();
    const onFix = vi.fn();
    const draft = makeDraft();
    render(
      <ParseCard
        draft={draft}
        committed={false}
        onConfirm={onConfirm}
        onFix={onFix}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /let me fix it/i }));
    expect(onFix).toHaveBeenCalledTimes(1);

    // After opening "fix" then immediately confirming without edits,
    // every field on the draft should still match the original.
    await userEvent.click(screen.getByRole('button', { name: /looks right/i }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const arg = onConfirm.mock.calls[0][0] as ParseDraft;
    expect(arg).toEqual(draft);
  });

  test('committed state hides the action buttons and shows "logged."', () => {
    render(
      <ParseCard
        draft={makeDraft()}
        committed
        onConfirm={() => {}}
        onFix={() => {}}
      />,
    );

    expect(screen.getByText(/logged\./i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /looks right/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /let me fix it/i }),
    ).not.toBeInTheDocument();
  });
});
