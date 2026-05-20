/**
 * EntryInsightBubble test — Day 13; tiered severity 2026-05-20.
 *
 * The component is intentionally trivial (renders one sentence in an
 * AI-bubble shell). What we guard is the *contract*:
 *   - Every severity tier renders the sentence verbatim.
 *   - `calm` keeps the quiet grey italic aside.
 *   - `elevated` / `alert` get the louder tinted treatment (amber /
 *     terracotta wash) so an overspending insight does not read like a
 *     calm observation.
 */

import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EntryInsightBubble } from '@/components/chat/EntryInsightBubble';

describe('EntryInsightBubble', () => {
  test('calm tier renders the sentence as a quiet italic aside', () => {
    const { container } = render(
      <EntryInsightBubble
        text="highest single dining spend this month."
        severity="calm"
      />,
    );
    expect(
      screen.getByText('highest single dining spend this month.'),
    ).toBeInTheDocument();
    expect(container.querySelector('.italic')).not.toBeNull();
  });

  test('elevated tier renders the amber wash treatment', () => {
    const { container } = render(
      <EntryInsightBubble
        text="on pace for about $40 over your monthly dining average."
        severity="elevated"
      />,
    );
    expect(
      screen.getByText(
        'on pace for about $40 over your monthly dining average.',
      ),
    ).toBeInTheDocument();
    expect(container.querySelector('.bg-warn-wash')).not.toBeNull();
  });

  test('alert tier renders the terracotta wash treatment', () => {
    const { container } = render(
      <EntryInsightBubble
        text="on pace for about $160 over your monthly dining average."
        severity="alert"
      />,
    );
    expect(
      screen.getByText(
        'on pace for about $160 over your monthly dining average.',
      ),
    ).toBeInTheDocument();
    expect(container.querySelector('.bg-over-wash')).not.toBeNull();
  });

  test('renders different sentences for different rule fires', () => {
    const sentences = [
      'highest single coffee shops spend this month.',
      '4th dining transaction this week — you usually have 2.',
      "you've used Chase Freedom for dining 3 times this week — Amex Gold earns 4x there.",
    ];
    for (const sentence of sentences) {
      const { unmount } = render(
        <EntryInsightBubble text={sentence} severity="calm" />,
      );
      expect(screen.getByText(sentence)).toBeInTheDocument();
      unmount();
    }
  });
});
