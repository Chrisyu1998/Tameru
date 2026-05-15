/** Currency / amount formatting helpers. */

/** Formats cents to "$47" (no decimals when whole) or "$47.50". */
export function formatMoney(cents: number, opts: { signed?: boolean } = {}): string {
  const { signed = false } = opts;
  const abs = Math.abs(cents) / 100;
  const formatted =
    abs % 1 === 0 ? `$${abs.toFixed(0)}` : `$${abs.toFixed(2)}`;
  if (!signed) return formatted;
  if (cents > 0) return `+${formatted}`;
  if (cents < 0) return `−${formatted}`;
  return formatted;
}

export function formatPercent(value: number, opts: { signed?: boolean } = {}): string {
  const { signed = true } = opts;
  const rounded = Math.round(value);
  if (!signed) return `${Math.abs(rounded)}%`;
  if (rounded > 0) return `+${rounded}%`;
  if (rounded < 0) return `−${Math.abs(rounded)}%`;
  return "0%";
}

/** Short date like "Apr 24" (current year) or "Apr 24, 2024" (other year). */
export function formatShortDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export function formatMonth(d: Date = new Date()): string {
  return d.toLocaleDateString("en-US", { month: "long" });
}
