/** Mock card preview data. Returned after a fake "AI" delay. */

export interface CardCategoryReward {
  category: string;
  value: string;
  /** moss = high confidence, amber = medium, terracotta = low */
  confidence: "moss" | "amber" | "terracotta";
}

export interface CardPreview {
  name: string;
  categories: CardCategoryReward[];
  sources: string[];
}

const FIXTURES: Record<string, Omit<CardPreview, "name">> = {
  "chase sapphire preferred": {
    categories: [
      { category: "Travel", value: "5x via portal", confidence: "moss" },
      { category: "Dining", value: "3x", confidence: "moss" },
      { category: "Streaming", value: "3x", confidence: "moss" },
      { category: "Online groceries", value: "3x", confidence: "amber" },
      { category: "Everything else", value: "1x", confidence: "moss" },
    ],
    sources: [
      "chase.com/sapphire-preferred",
      "thepointsguy.com",
      "doctorofcredit.com",
    ],
  },
  "amex gold": {
    categories: [
      { category: "Restaurants worldwide", value: "4x", confidence: "moss" },
      { category: "U.S. supermarkets", value: "4x (cap $25k/yr)", confidence: "moss" },
      { category: "Flights booked direct", value: "3x", confidence: "moss" },
      { category: "Prepaid hotels (Amex Travel)", value: "2x", confidence: "amber" },
      { category: "Everything else", value: "1x", confidence: "moss" },
    ],
    sources: [
      "americanexpress.com/gold",
      "thepointsguy.com",
      "uponarriving.com",
    ],
  },
  "citi double cash": {
    categories: [
      { category: "All purchases", value: "1% on buy", confidence: "moss" },
      { category: "All purchases", value: "1% on pay", confidence: "moss" },
      { category: "Travel via Citi portal", value: "5x (limited time)", confidence: "amber" },
      { category: "Foreign transactions", value: "3% fee", confidence: "terracotta" },
      { category: "Welcome bonus", value: "Varies", confidence: "amber" },
    ],
    sources: ["citi.com/double-cash", "nerdwallet.com"],
  },
};

const DEFAULT_FIXTURE: Omit<CardPreview, "name"> = {
  categories: [
    { category: "Dining", value: "3x", confidence: "amber" },
    { category: "Travel", value: "2x", confidence: "amber" },
    { category: "Groceries", value: "2x", confidence: "moss" },
    { category: "Gas", value: "1x", confidence: "moss" },
    { category: "Everything else", value: "1x", confidence: "terracotta" },
  ],
  sources: ["issuer website", "perplexity.ai"],
};

export async function fetchCardPreview(name: string): Promise<CardPreview> {
  await new Promise((r) => setTimeout(r, 750));
  const key = name.trim().toLowerCase();
  const fixture = FIXTURES[key] ?? DEFAULT_FIXTURE;
  return { name: name.trim(), ...fixture };
}
