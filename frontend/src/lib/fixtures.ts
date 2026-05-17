import type { Category } from "./categories";
import type { CardIssuer } from "./cardsApi";

/** Loyalty / points programs surfaced as a small chip on each card. */
export type CardProgram = "UR" | "MR" | "Bilt" | "ThankYou" | "Cash";

export interface CardMultiplier {
  /** Free-form, e.g. "dining", "groceries", "travel" */
  label: string;
  /** Whole-number multiplier, e.g. 4 for "4×" */
  factor: number;
}

export interface Card {
  id: string;
  name: string;
  /** last 4 digits */
  last4: string;
  /** Stripe color for the left edge of card tiles. Optional for backwards-compat. */
  color?: string;
  /** Loyalty program chip. */
  program?: CardProgram;
  /**
   * Issuing bank — surfaced as a neutral chip alongside `program` on the
   * cards-page tile (Day 14 follow-up). Closed-enum so chips and selects
   * can title-case via `ISSUER_LABELS` without lookup-code churn.
   */
  issuer?: CardIssuer;
  /** Earn rate chips like "4× dining". */
  multipliers?: CardMultiplier[];
}

export interface Transaction {
  id: string;
  merchant: string;
  /** Positive number, in home-currency cents to keep math precise. */
  amountCents: number;
  /** ISO date string YYYY-MM-DD */
  date: string;
  cardId: string;
  category: Category;
  /**
   * True when tameru detected this transaction from a recurring pattern
   * (e.g. Spotify on the 5th of every month) rather than the user logging it.
   * Surfaces as a small 🔄 badge on transaction rows so it's always clear
   * what the user typed vs. what tameru inferred.
   */
  autoLogged?: boolean;
}

// v1 has no /cards backend yet; the Cards page renders an empty state and
// the chat picker shows only "Other / Cash" until a real cards feed lands.
// Transactions logged without a card persist as card_id = NULL on the
// server (transactions.sql:9 — column is nullable).
export const FIXTURE_CARDS: Card[] = [];

/**
 * Helper to build dated fixtures relative to "today" so the dashboard
 * always feels current. Negative `daysAgo` = future (unused).
 */
function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

const t = (
  merchant: string,
  amount: number,
  daysOffset: number,
  cardId: string,
  category: Category,
  id: string,
  opts: { autoLogged?: boolean } = {}
): Transaction => ({
  id,
  merchant,
  amountCents: Math.round(amount * 100),
  date: daysAgo(daysOffset),
  cardId,
  category,
  ...(opts.autoLogged ? { autoLogged: true } : {}),
});

export const FIXTURE_TRANSACTIONS: Transaction[] = [
  // Dining (running above usual)
  t("Roji Coffee", 5.5, 0, "card-amex", "Dining", "tx-1"),
  t("Lupa", 84.0, 1, "card-csp", "Dining", "tx-2"),
  t("Roji Coffee", 5.5, 1, "card-amex", "Dining", "tx-3"),
  t("Frankel's Deli", 18.4, 3, "card-amex", "Dining", "tx-4"),
  t("Misi", 142.0, 5, "card-csp", "Dining", "tx-5"),
  t("Roji Coffee", 5.5, 5, "card-amex", "Dining", "tx-6"),
  t("Wayan", 96.5, 8, "card-csp", "Dining", "tx-7"),
  t("Sahadi's Lunch", 14.0, 9, "card-amex", "Dining", "tx-8"),
  t("Roji Coffee", 11.0, 10, "card-amex", "Dining", "tx-9"),
  t("Le Crocodile", 168.0, 12, "card-csp", "Dining", "tx-10"),

  // Groceries (slightly below usual)
  t("Whole Foods", 64.2, 0, "card-citi", "Groceries", "tx-11"),
  t("Trader Joe's", 38.4, 4, "card-citi", "Groceries", "tx-12"),
  t("Sahadi's", 22.1, 7, "card-amex", "Groceries", "tx-13"),
  t("Whole Foods", 51.9, 11, "card-citi", "Groceries", "tx-14"),
  t("Greenmarket", 19.0, 14, "card-citi", "Groceries", "tx-15"),

  // Transit (a bit above usual)
  t("MTA OMNY", 2.9, 0, "card-citi", "Transit", "tx-16"),
  t("Lyft", 18.4, 2, "card-csp", "Transit", "tx-17"),
  t("MTA OMNY", 2.9, 2, "card-citi", "Transit", "tx-18"),
  t("Lyft", 24.6, 6, "card-csp", "Transit", "tx-19"),
  t("MTA OMNY", 33.0, 9, "card-citi", "Transit", "tx-20"),
  t("Revel", 14.0, 11, "card-csp", "Transit", "tx-21"),

  // Shopping (below usual)
  t("Uniqlo", 49.5, 6, "card-citi", "Shopping", "tx-22"),
  t("McNally Jackson", 28.4, 13, "card-amex", "Shopping", "tx-23"),

  // Entertainment
  t("Metrograph", 17.0, 4, "card-amex", "Entertainment", "tx-24"),
  t("Brooklyn Steel", 65.0, 8, "card-csp", "Entertainment", "tx-25"),

  // Subscriptions — auto-detected recurring charges (5th-of-month etc.)
  t("Spotify", 11.99, 2, "card-citi", "Subscriptions", "tx-26", { autoLogged: true }),
  t("NYT", 17.0, 9, "card-citi", "Subscriptions", "tx-27", { autoLogged: true }),
  t("iCloud+", 2.99, 12, "card-citi", "Subscriptions", "tx-28", { autoLogged: true }),

  // Utilities
  t("Con Edison", 89.4, 10, "card-citi", "Utilities", "tx-29"),

  // Health
  t("CVS", 24.0, 5, "card-amex", "Health", "tx-30"),

  // Travel
  t("Amtrak", 138.0, 15, "card-csp", "Travel", "tx-31"),

  // Other
  t("Etsy", 32.0, 7, "card-amex", "Other", "tx-32"),
];

