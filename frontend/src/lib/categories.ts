/**
 * Closed category list — mirrors backend `ALLOWED_CATEGORIES` exactly
 * (`app/prompts/categories.py`). The backend is the source of truth: the
 * MCC-aligned taxonomy here is what drives card-reward multiplier matching
 * (DESIGN.md §6.2). Any drift here re-introduces the 422 from
 * /transactions/confirm that Day 10b was created to fix.
 */

import type { SketchIconKind } from "@/components/SketchIcon";

export const CATEGORIES = [
  "Groceries",
  "Dining",
  "Coffee Shops",
  "Gas",
  "Transit",
  "Travel",
  "Streaming",
  "Memberships",
  "Entertainment",
  "Shopping",
  "Drugstores",
  "Home",
  "Utilities",
  "Health",
  "Other",
] as const;

export type Category = (typeof CATEGORIES)[number];

/** Color tokens per category, used by the donut + category list. */
export const CATEGORY_TINT: Record<Category, string> = {
  Groceries: "var(--moss)",
  Dining: "var(--over)",
  "Coffee Shops": "var(--over)",
  Gas: "var(--warn)",
  Transit: "var(--ink-secondary)",
  Travel: "var(--moss-deep)",
  Streaming: "var(--ink-tertiary)",
  Memberships: "var(--ink-quaternary)",
  Entertainment: "var(--warn)",
  Shopping: "var(--ink-tertiary)",
  Drugstores: "var(--moss)",
  Home: "var(--moss-deep)",
  Utilities: "var(--warn)",
  Health: "var(--moss)",
  Other: "var(--ink-quaternary)",
};

/**
 * Hand-drawn sketch glyph per category. Used on /breakdown row tiles
 * to add personality to a list-heavy page. Some glyphs repeat where
 * the icon set doesn't have a perfect match — duplication beats
 * inventing a glyph for v1.
 */
export const CATEGORY_SKETCH: Record<Category, SketchIconKind> = {
  Groceries: "bag",
  Dining: "fork",
  "Coffee Shops": "receipt",
  Gas: "bolt",
  Transit: "car",
  Travel: "plane",
  Streaming: "ticket",
  Memberships: "repeat",
  Entertainment: "sparkle",
  Shopping: "bag",
  Drugstores: "heart",
  Home: "home",
  Utilities: "bolt",
  Health: "heart",
  Other: "dot",
};
