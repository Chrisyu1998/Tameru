/**
 * SubscriptionParseCard render test — Day 19.
 *
 * Smoke-tests the parse-card render across the lifecycle states:
 *   - Fresh proposal: amount + frequency editable, "looks right"
 *     enabled when amount validates.
 *   - Committed `active`: badge reads "tracking."; inputs hidden.
 *   - Committed `paused`: badge reads "paused.".
 *   - Committed `cancelled`: badge reads "cancelled.".
 *   - Offline pending: "queued — syncs when online."; button hidden.
 *
 * Cards are passed in to populate the picker; the component otherwise
 * stays presentational.
 */

import { describe, expect, test, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { SubscriptionParseCard } from "@/components/chat/SubscriptionParseCard";
import type { SubscriptionParseDraft } from "@/lib/chat";
import type { Card } from "@/lib/fixtures";

const DRAFT: SubscriptionParseDraft = {
  name: "Netflix",
  amount: "15.99",
  frequency: "monthly",
  startDate: "2026-05-18",
  nextBillingDate: "2026-06-18",
  category: "Streaming",
  cardId: null,
  clientRequestId: "00000000-0000-0000-0000-0000000019aa",
};

const CARDS: Card[] = [];

describe("SubscriptionParseCard", () => {
  test("fresh proposal renders editable amount + cadence and enables confirm", () => {
    const onConfirm = vi.fn();
    const { getByText, getByDisplayValue } = render(
      <SubscriptionParseCard
        draft={DRAFT}
        cards={CARDS}
        committed={false}
        onConfirm={onConfirm}
      />,
    );
    // Headline visible.
    expect(getByText("Netflix")).toBeTruthy();
    // Amount field surfaces the editable value.
    const amountInput = getByDisplayValue("15.99") as HTMLInputElement;
    expect(amountInput.tagName).toBe("INPUT");

    // Tapping "looks right" fires onConfirm with the local draft.
    fireEvent.click(getByText("looks right"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toMatchObject({
      name: "Netflix",
      amount: "15.99",
      cardId: null,
    });
  });

  test("committed active state shows 'tracking.' badge", () => {
    const { getByText, queryByText } = render(
      <SubscriptionParseCard
        draft={DRAFT}
        cards={CARDS}
        committed={true}
        committedState="active"
        onConfirm={vi.fn()}
      />,
    );
    expect(getByText("tracking.")).toBeTruthy();
    expect(queryByText("looks right")).toBeNull();
  });

  test("committed paused state shows 'paused.' badge", () => {
    const { getByText } = render(
      <SubscriptionParseCard
        draft={DRAFT}
        cards={CARDS}
        committed={true}
        committedState="paused"
        onConfirm={vi.fn()}
      />,
    );
    expect(getByText("paused.")).toBeTruthy();
  });

  test("committed cancelled state shows 'cancelled.' badge", () => {
    const { getByText } = render(
      <SubscriptionParseCard
        draft={DRAFT}
        cards={CARDS}
        committed={true}
        committedState="cancelled"
        onConfirm={vi.fn()}
      />,
    );
    expect(getByText("cancelled.")).toBeTruthy();
  });

  test("pendingSync (offline queued) shows the queued badge and hides confirm", () => {
    const { getByText, queryByText } = render(
      <SubscriptionParseCard
        draft={DRAFT}
        cards={CARDS}
        committed={false}
        pendingSync={true}
        onConfirm={vi.fn()}
      />,
    );
    expect(getByText("queued — syncs when online.")).toBeTruthy();
    expect(queryByText("looks right")).toBeNull();
  });
});
