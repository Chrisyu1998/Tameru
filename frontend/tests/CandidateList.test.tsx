/**
 * CandidateCards test — Day 10b §8.
 *
 * Tap a candidate row → onSelect fires with that exact Transaction object,
 * so the page's "open edit sheet for the right id" wiring works.
 */

import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CandidateCards } from '@/components/chat/CandidateCards';
import type { Transaction } from '@/lib/fixtures';

const candidates: Transaction[] = [
  {
    id: 'tx-1',
    merchant: 'Lupa',
    amountCents: 4200,
    date: '2026-05-10',
    cardId: 'card-amex',
    category: 'Dining',
  },
  {
    id: 'tx-2',
    merchant: 'Trader Joe’s',
    amountCents: 6450,
    date: '2026-05-09',
    cardId: 'card-citi',
    category: 'Groceries',
  },
];

describe('CandidateCards', () => {
  test('renders the preface and one row per candidate', () => {
    render(
      <CandidateCards
        preface="here are matches:"
        candidates={candidates}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText('here are matches:')).toBeInTheDocument();
    expect(screen.getByText('Lupa')).toBeInTheDocument();
    expect(screen.getByText('Trader Joe’s')).toBeInTheDocument();
  });

  test('tapping a row opens the edit sheet with the right id (onSelect receives that tx)', async () => {
    const onSelect = vi.fn();
    render(
      <CandidateCards
        preface="found these:"
        candidates={candidates}
        onSelect={onSelect}
      />,
    );

    await userEvent.click(screen.getByText('Lupa'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toEqual(candidates[0]);

    await userEvent.click(screen.getByText('Trader Joe’s'));
    expect(onSelect).toHaveBeenCalledTimes(2);
    expect(onSelect.mock.calls[1][0]).toEqual(candidates[1]);
  });

  test('shows an empty-state copy when there are no candidates', () => {
    render(
      <CandidateCards
        preface="searched for that"
        candidates={[]}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText(/nothing matched/i)).toBeInTheDocument();
  });

  test('collapses past 5 rows behind a "+N more" button', async () => {
    const many: Transaction[] = Array.from({ length: 8 }, (_, i) => ({
      id: `tx-${i}`,
      merchant: `Merchant ${i}`,
      amountCents: 100 * (i + 1),
      date: '2026-05-10',
      cardId: 'card-amex',
      category: 'Dining',
    }));
    render(
      <CandidateCards
        preface="many matches"
        candidates={many}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText('Merchant 0')).toBeInTheDocument();
    expect(screen.getByText('Merchant 4')).toBeInTheDocument();
    expect(screen.queryByText('Merchant 5')).not.toBeInTheDocument();
    const more = screen.getByRole('button', { name: /\+3 more/i });
    await userEvent.click(more);
    expect(screen.getByText('Merchant 7')).toBeInTheDocument();
  });
});
