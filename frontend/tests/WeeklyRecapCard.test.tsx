/**
 * WeeklyRecapCard render test — 2026-07-03 (DESIGN.md §6.2 / §6.4).
 *
 * The pinned "this week" recap card. Covers:
 *   - Expanded render: headline total, colored below/above-average delta line,
 *     top-category line, observation + nudge prose.
 *   - Absent top category / nudge degrade cleanly (no crash, no empty line).
 *   - Collapse toggle persists the per-week flag to localStorage, and a card
 *     for an already-collapsed week starts as the one-line pill.
 *
 * Uses the global i18n setup (tests/setup.ts), so `t()` returns the real
 * English copy and money formats via the default USD/en locale.
 */

import { beforeEach, describe, expect, test } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { WeeklyRecapCard } from "@/components/chat/WeeklyRecapCard";
import type { WeeklyRecap } from "@/lib/chatApi";

// Node 25's experimental localStorage is half-mounted (missing setItem/getItem),
// so install a deterministic in-memory shim per test — same pattern as
// tests/voice.test.ts. A fresh shim each `beforeEach` also resets state.
function installMemoryStorage() {
  const store: Record<string, string> = {};
  const shim: Storage = {
    get length() {
      return Object.keys(store).length;
    },
    clear() {
      for (const k of Object.keys(store)) delete store[k];
    },
    getItem(key: string) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    key(i: number) {
      return Object.keys(store)[i] ?? null;
    },
    removeItem(key: string) {
      delete store[key];
    },
    setItem(key: string, value: string) {
      store[key] = String(value);
    },
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: shim });
}

const RECAP: WeeklyRecap = {
  dedup_week: "2026-07-07",
  week_start: "2026-07-07",
  week_end: "2026-07-13",
  week_total: "180",
  baseline_avg: "200",
  top_category: "Dining",
  top_category_total: "80",
  top_category_baseline: "60",
  home_currency: "USD",
  ui_language: "en",
  observation: "Spending was steady this week.",
  nudge: "Dining was your top category.",
};

describe("WeeklyRecapCard", () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  test("expanded card renders total, below-average delta, top line, prose", () => {
    const { getByText } = render(<WeeklyRecapCard recap={RECAP} />);
    expect(getByText("this week")).toBeTruthy();
    expect(getByText("$180")).toBeTruthy(); // week total
    // Spent under the weekly average → green "below" line.
    expect(getByText("$20 below your weekly average")).toBeTruthy();
    expect(getByText("Dining led at $80 · $20 above usual")).toBeTruthy();
    expect(getByText("Spending was steady this week.")).toBeTruthy();
    expect(getByText("Dining was your top category.")).toBeTruthy();
  });

  test("fractional decimal-string amounts format with cents (2 dp)", () => {
    // The wire carries decimal strings; whole-dollar fixtures elsewhere don't
    // exercise the sub-dollar formatting path. $234.56 vs $180.00 → $54.56 over.
    const fractional: WeeklyRecap = {
      ...RECAP,
      week_total: "234.56",
      baseline_avg: "180.00",
      top_category_total: "87.50",
      top_category_baseline: "65.00",
    };
    const { getByText } = render(<WeeklyRecapCard recap={fractional} />);
    expect(getByText("$234.56")).toBeTruthy(); // week total, 2 dp
    expect(getByText("$54.56 above your weekly average")).toBeTruthy();
    expect(getByText("Dining led at $87.50 · $22.50 above usual")).toBeTruthy();
  });

  test("above-average week uses the 'above' delta copy", () => {
    const over: WeeklyRecap = {
      ...RECAP,
      week_total: "260",
      baseline_avg: "200",
    };
    const { getByText } = render(<WeeklyRecapCard recap={over} />);
    expect(getByText("$60 above your weekly average")).toBeTruthy();
  });

  test("no top category and no nudge render without a top line or crash", () => {
    const bare: WeeklyRecap = {
      ...RECAP,
      top_category: null,
      top_category_total: null,
      top_category_baseline: null,
      nudge: null,
    };
    const { getByText, queryByText } = render(<WeeklyRecapCard recap={bare} />);
    expect(getByText("Spending was steady this week.")).toBeTruthy();
    expect(queryByText(/led at/)).toBeNull();
    expect(queryByText("Dining was your top category.")).toBeNull();
  });

  test("collapse hides the prose, shows the pill, and persists the flag", () => {
    const { getByLabelText, queryByText } = render(
      <WeeklyRecapCard recap={RECAP} />,
    );
    fireEvent.click(getByLabelText("hide weekly recap"));
    expect(queryByText("Spending was steady this week.")).toBeNull();
    expect(getByLabelText("show weekly recap")).toBeTruthy();
    expect(window.localStorage.getItem("tameru-recap-collapsed-2026-07-07")).toBe(
      "1",
    );
  });

  test("a week already marked collapsed starts as the pill", () => {
    window.localStorage.setItem("tameru-recap-collapsed-2026-07-07", "1");
    const { getByLabelText, queryByText } = render(
      <WeeklyRecapCard recap={RECAP} />,
    );
    expect(getByLabelText("show weekly recap")).toBeTruthy();
    expect(queryByText("Spending was steady this week.")).toBeNull();
  });
});
