/** Shared types + currency catalog for onboarding. */

export type Currency =
  | "USD"
  | "EUR"
  | "GBP"
  | "CAD"
  | "AUD"
  | "JPY"
  | "CHF"
  | "SGD"
  | "TWD";

export interface CurrencyOption {
  code: Currency;
  name: string;
  symbol: string;
}

export const CURRENCIES: CurrencyOption[] = [
  { code: "USD", name: "US Dollar", symbol: "$" },
  { code: "EUR", name: "Euro", symbol: "€" },
  { code: "GBP", name: "British Pound", symbol: "£" },
  { code: "CAD", name: "Canadian Dollar", symbol: "$" },
  { code: "AUD", name: "Australian Dollar", symbol: "$" },
  { code: "JPY", name: "Japanese Yen", symbol: "¥" },
  { code: "CHF", name: "Swiss Franc", symbol: "Fr" },
  { code: "SGD", name: "Singapore Dollar", symbol: "$" },
  { code: "TWD", name: "Taiwan Dollar", symbol: "NT$" },
];

export type OnboardingStep =
  | "splash"
  | "philosophy"
  | "signin"
  | "currency"
  | "addCard"
  | "csvImport"
  | "csvProcessing";
