import { apiJson, apiFetch } from './api';
import type { Category } from './categories';
import type { InsightSeverity } from './chat';
import type { Transaction } from './fixtures';

/*
 * Typed wrappers for /transactions (app/routes/transactions.py).
 *
 * The backend speaks Decimal amounts (rendered as JSON strings) and
 * underscored field names; the UI Lovable shipped speaks cents-as-number
 * and camelCase. We translate at the boundary here so nothing downstream
 * needs to know about the wire shape.
 *
 * Cards are not mapped at all — there's no /cards endpoint in v1, so cards
 * stay on the local fixture path (lib/ledger.ts).
 */

export interface TransactionRowWire {
  id: string;
  user_id: string;
  card_id: string | null;
  subscription_id: string | null;
  merchant: string;
  amount: string; // Decimal serialized as string
  date: string; // YYYY-MM-DD
  category: Category;
  gemini_suggestion: string | null;
  source: string;
  notes: string | null;
  client_request_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TransactionListWire {
  items: TransactionRowWire[];
  has_more: boolean;
}

/** Entry-moment insight wire shape — mirrors `EntryMomentInsight`
 * (app/models/transactions.py). `text` is the one-sentence bubble copy;
 * `severity` drives `EntryInsightBubble`'s tiered visual treatment. */
export interface EntryMomentInsightWire {
  text: string;
  severity: InsightSeverity;
}

export interface TransactionConfirmResponseWire {
  transaction: TransactionRowWire;
  insight: EntryMomentInsightWire | null;
}

/**
 * Wire `amount` (Decimal as string, dollars) → UI `amountCents` (integer).
 * Multiplying by 100 then rounding handles both "5" and "5.50" cleanly;
 * Postgres stores `numeric(12,2)` so we never see more than 2 fractional
 * digits anyway.
 */
export function amountToCents(amount: string): number {
  const n = Number(amount);
  if (!Number.isFinite(n)) return 0;
  return Math.round(n * 100);
}

/** UI cents → wire decimal-dollars string for confirm/PATCH bodies. */
export function centsToAmount(cents: number): string {
  return (cents / 100).toFixed(2);
}

/*
 * Lowercase UUID v1-v5 string match. v1 has no /cards endpoint, so the
 * only card IDs the backend will accept are real UUIDs. The parse-card
 * UI inherits Lovable's local FIXTURE_CARDS which use slugs like
 * "card-amex" — those need to be downgraded to null at the wire boundary
 * or POST /transactions/confirm 422s on the Pydantic UUID validator.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function looksLikeUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}
export function sanitizeCardId(value: string | null | undefined): string | null {
  return looksLikeUuid(value) ? value : null;
}

/**
 * Map the server row to the camelCase shape the imported UI expects. Cards
 * are referenced by `cardId` (string | unknown), and the `source` column
 * surfaces in the UI as the `autoLogged` boolean — anything except 'nlp'
 * counts as auto (subscription auto-logger, csv import, etc.).
 */
export function fromWire(row: TransactionRowWire): Transaction {
  return {
    id: row.id,
    merchant: row.merchant,
    amountCents: amountToCents(row.amount),
    date: row.date,
    cardId: row.card_id ?? '',
    category: row.category,
    autoLogged: row.source !== 'nlp',
  };
}

export async function listTransactions(): Promise<Transaction[]> {
  // Request the full window the backend allows (MAX_LIMIT = 500) rather
  // than the default 50. The breakdown month picker filters this list
  // client-side, and rows come back date-desc — so a 50-row default
  // truncates OLDER months first, which would make "last month" look
  // empty for anyone with a busy current month. At v1 scale (~10 users,
  // manual entry) 500 covers every user comfortably; a user past 500
  // transactions sees the oldest truncated (the backend's own MAX_LIMIT
  // ceiling), at which point the list needs real limit+offset pagination.
  const wire = await apiJson<TransactionListWire>('/transactions?limit=500');
  return wire.items.map(fromWire);
}

export interface ConfirmTransactionBody {
  merchant: string;
  amount: string; // decimal dollars as string
  date: string;   // YYYY-MM-DD
  card_id: string | null;
  category: Category;
  notes: string | null;
  gemini_suggestion: string | null;
  client_request_id: string;
}

export interface ConfirmTransactionResult {
  transaction: Transaction;
  // Deterministic entry-moment insight (sentence + severity tier) when a
  // rule fires; null on first-in-category, within-noise deltas, saturated
  // rate limits, and on idempotent replay (Day 13).
  insight: EntryMomentInsightWire | null;
}

export async function confirmTransaction(
  body: ConfirmTransactionBody,
): Promise<ConfirmTransactionResult> {
  // The server strips fields it doesn't accept; we only send the documented
  // ones so extra=forbid in TransactionConfirmRequest stays happy.
  const wire = await apiJson<TransactionConfirmResponseWire>(
    '/transactions/confirm',
    {
      method: 'POST',
      body,
    },
  );
  return { transaction: fromWire(wire.transaction), insight: wire.insight };
}

export interface PatchTransactionBody {
  merchant?: string;
  amount?: string;
  date?: string;
  card_id?: string | null;
  category?: Category;
  notes?: string | null;
}

export async function patchTransaction(
  id: string,
  patch: PatchTransactionBody,
): Promise<Transaction> {
  const wire = await apiJson<TransactionRowWire>(`/transactions/${id}`, {
    method: 'PATCH',
    body: patch,
  });
  return fromWire(wire);
}

export async function deleteTransaction(id: string): Promise<void> {
  const res = await apiFetch(`/transactions/${id}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 204) {
    throw new Error(`DELETE /transactions/${id} failed: ${res.status}`);
  }
}
