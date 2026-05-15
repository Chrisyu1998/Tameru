/** Closed category list — used by store + edit sheet. */

import type { SketchIconKind } from "@/components/SketchIcon";

export const CATEGORIES = [
  "Groceries",
  "Dining",
  "Transportation",
  "Travel",
  "Entertainment",
  "Shopping",
  "Utilities",
  "Health",
  "Subscriptions",
  "Other",
] as const;

export type Category = (typeof CATEGORIES)[number];

/** Color tokens per category, used by the donut + category list. */
export const CATEGORY_TINT: Record<Category, string> = {
  Groceries: "var(--moss)",
  Dining: "var(--over)",
  Transportation: "var(--ink-secondary)",
  Travel: "var(--moss-deep)",
  Entertainment: "var(--warn)",
  Shopping: "var(--ink-tertiary)",
  Utilities: "var(--warn)",
  Health: "var(--moss)",
  Subscriptions: "var(--ink-quaternary)",
  Other: "var(--ink-quaternary)",
};

/**
 * Hand-drawn sketch glyph per category. Used on /breakdown row tiles
 * to add personality to a list-heavy page. Health/Other fall back to
 * the bag/dot for visual variety.
 */
export const CATEGORY_SKETCH: Record<Category, SketchIconKind> = {
  Groceries: "bag",
  Dining: "fork",
  Transportation: "car",
  Travel: "plane",
  Entertainment: "ticket",
  Shopping: "bag",
  Utilities: "bolt",
  Health: "heart",
  Subscriptions: "repeat",
  Other: "dot",
};
