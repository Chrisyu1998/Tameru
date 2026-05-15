/**
 * In-memory AI fact store. Mock — no backend yet.
 * Capacity is fixed at 60 facts; UI surfaces an amber warning >80% full.
 */

export type MemoryCategory =
  | "card preference"
  | "goal"
  | "spending pattern"
  | "person"
  | "place"
  | "rule";

export interface MemoryFact {
  id: string;
  category: MemoryCategory;
  text: string;
  /** Where this fact came from, e.g. "saved apr 12 from chat". */
  provenance: string;
}

export const MEMORY_CAPACITY = 60;

export const seedMemoryFacts: MemoryFact[] = [
  {
    id: "m-1",
    category: "card preference",
    text: "use the sapphire for dining and travel — better points multiplier.",
    provenance: "saved apr 12 from chat",
  },
  {
    id: "m-2",
    category: "goal",
    text: "keep monthly dining under $400.",
    provenance: "saved apr 03 from chat",
  },
  {
    id: "m-3",
    category: "spending pattern",
    text: "groceries usually run $90–110 weekly at trader joe's.",
    provenance: "inferred mar 28 from ledger",
  },
  {
    id: "m-4",
    category: "person",
    text: "M is your partner — split lunches are roughly 50/50.",
    provenance: "saved apr 09 from chat",
  },
  {
    id: "m-5",
    category: "place",
    text: "lupa is your favorite italian spot — usually $80–120 for two.",
    provenance: "inferred apr 02 from ledger",
  },
  {
    id: "m-6",
    category: "rule",
    text: "subscriptions over $15/mo should be flagged for review.",
    provenance: "saved mar 22 from chat",
  },
  {
    id: "m-7",
    category: "card preference",
    text: "amex gold for groceries — 4x points at supermarkets.",
    provenance: "saved apr 14 from chat",
  },
  {
    id: "m-8",
    category: "goal",
    text: "save $500/month toward a kyoto trip in october.",
    provenance: "saved apr 18 from chat",
  },
  {
    id: "m-9",
    category: "spending pattern",
    text: "weekend coffee runs average about $32/month.",
    provenance: "inferred apr 06 from ledger",
  },
  {
    id: "m-10",
    category: "rule",
    text: "ask before logging anything over $200.",
    provenance: "saved apr 01 from chat",
  },
];

// Pad up to a believable number for the capacity counter.
const fillerCategories: MemoryCategory[] = [
  "spending pattern",
  "place",
  "person",
  "rule",
];
const fillerTexts = [
  "morning runs to blue bottle on tuesdays.",
  "uber rides to brooklyn average $24.",
  "splits brunch with J on sundays.",
  "max $50 on takeout per weekday.",
  "blue point pharmacy for refills.",
  "amazon orders cluster on wednesday nights.",
  "gym membership renews on the 5th.",
  "annual netflix bump expected in june.",
  "spotify family plan is shared with sister.",
  "library fines should be ignored, not flagged.",
  "domain renewals come due in november.",
  "dry cleaner on mott st, ~$28 per drop.",
  "groceries spike before dinner parties.",
  "gas fillups every ~10 days.",
  "ice cream runs always after a long walk.",
  "movie nights are friday only.",
  "haircut every 6 weeks at $45.",
  "morning bagel + coffee = $9 baseline.",
  "rent splits 60/40 with M.",
  "venmo from R is rent reimbursement, not income.",
  "amazon returns refunded within 7 days.",
  "thursday is farmers market day.",
  "season tickets paid in march only.",
  "bookstore visits stay under $40.",
  "wine club ships quarterly, ~$110.",
  "favorite ramen spot is $18/bowl.",
  "ride-shares after 11pm count as 'late'.",
  "no impulse buys on monday mornings.",
];

const filler: MemoryFact[] = fillerTexts.map((t, i) => ({
  id: `m-f${i + 1}`,
  category: fillerCategories[i % fillerCategories.length],
  text: t,
  provenance: i % 3 === 0 ? "saved earlier from chat" : "inferred from ledger",
}));

export const initialMemoryFacts: MemoryFact[] = [
  ...seedMemoryFacts,
  ...filler,
];
