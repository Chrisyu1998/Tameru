/**
 * EntryInsightBubble test — Day 13.
 *
 * The component is intentionally trivial (renders one string in a quiet
 * AI-bubble shell). What we're guarding is the *contract*:
 *   - When the `insight` field on /transactions/confirm is a string, the
 *     bubble renders the sentence verbatim.
 *   - When `insight` is null upstream, no bubble appears (chatStore's
 *     responsibility — we cover that in a chatStore-level test).
 */

import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EntryInsightBubble } from '@/components/chat/EntryInsightBubble';

describe('EntryInsightBubble', () => {
  test('renders the supplied insight string', () => {
    render(<EntryInsightBubble text="highest single dining spend this month." />);
    expect(
      screen.getByText('highest single dining spend this month.'),
    ).toBeInTheDocument();
  });

  test('renders different sentences for different rule fires', () => {
    const sentences = [
      'highest single coffee shops spend this month.',
      '4th dining transaction this week — you usually have 2.',
      'this puts you $23 above your monthly dining average with 12 days left.',
      "you've used Chase Freedom for dining 3 times this week — Amex Gold earns 4x there.",
    ];
    for (const sentence of sentences) {
      const { unmount } = render(<EntryInsightBubble text={sentence} />);
      expect(screen.getByText(sentence)).toBeInTheDocument();
      unmount();
    }
  });
});
