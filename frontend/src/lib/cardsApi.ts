import { apiFetch, apiJson, ApiError } from './api';

/*
 * Day 14 — typed client for /cards/* endpoints.
 *
 * Mirrors app/models/cards.py exactly. The shapes here are wire shapes —
 * if the backend Pydantic model changes, change this file in the same
 * commit. Components consume the types via `import type { ... } from
 * '@/lib/cardsApi'` to avoid duplicating the field set.
 */

export type CardNetwork = 'visa' | 'mastercard' | 'amex' | 'discover' | 'other';
export type CardProgram = 'UR' | 'MR' | 'TYP' | 'Bilt' | 'Other';

// Closed-enum issuer. Mirrors `app/models/cards.py::CardIssuer` and the
// DB CHECK constraint installed by migration
// 20260516140000_cards_uniqueness_by_issuer.sql. If you add a value here,
// update the backend Literal + DB CHECK in the same change.
export type CardIssuer =
  | 'chase'
  | 'amex'
  | 'citi'
  | 'capital_one'
  | 'discover'
  | 'bank_of_america'
  | 'wells_fargo'
  | 'usaa'
  | 'bilt'
  | 'barclays'
  | 'us_bank'
  | 'synchrony'
  | 'other';

// Friendly labels for the UI — the enum stays canonical snake_case for the
// wire / DB; the display map exists so chips and selects show titlecase
// without sprinkling lookup code through components.
export const ISSUER_LABELS: Record<CardIssuer, string> = {
  chase: 'Chase',
  amex: 'Amex',
  citi: 'Citi',
  capital_one: 'Capital One',
  discover: 'Discover',
  bank_of_america: 'Bank of America',
  wells_fargo: 'Wells Fargo',
  usaa: 'USAA',
  bilt: 'Bilt',
  barclays: 'Barclays',
  us_bank: 'US Bank',
  synchrony: 'Synchrony',
  other: 'Other',
};

export const ISSUERS: readonly CardIssuer[] = Object.keys(
  ISSUER_LABELS,
) as CardIssuer[];

export interface CardLookupResult {
  program: CardProgram | null;
  network: CardNetwork | null;
  multipliers: Record<string, number>;
  annual_fee: string | null;
  issuer: CardIssuer | null;
  source_urls: string[];
  needs_manual: boolean;
  raw_text?: string | null;
}

export interface CardLookupResponse {
  name: string;
  lookup: CardLookupResult;
}

export interface CardProposal {
  network: CardNetwork;
  // Optional on the proposal-tool return shape (propose_card may not have
  // the user's last 4 yet). The parse-card UI MUST collect it before
  // `POST /cards/confirm`, which 422s if it's still missing at commit.
  last_four: string | null;
  name: string;
  issuer: CardIssuer;
  program: CardProgram;
  multipliers: Record<string, number>;
  annual_fee?: string | null;
  source_urls: string[];
  color?: string | null;
  alias?: string | null;
  needs_manual: boolean;
  /**
   * Stable per-proposal join key, minted server-side at `propose_card`
   * time. The frontend posts it back verbatim at `/cards/confirm`; the
   * row stores it in `cards.client_request_id`. Drives the chat
   * rehydrate annotation's 1:1 join (no name-collision ambiguity) and
   * the offline-queue drain's in-memory match. Same name as the
   * transaction-side field, same lifecycle, different role: cards use
   * crid as a join key, transactions use it as an idempotency token —
   * but the wire shape is identical.
   */
  client_request_id: string;
}

export type CardStatus = 'active' | 'deleted';

export interface CardRow {
  id: string;
  user_id: string;
  name: string;
  issuer: CardIssuer;
  network: CardNetwork;
  program: CardProgram;
  multipliers: Record<string, number>;
  annual_fee: string | null;
  last_four: string | null;
  color: string | null;
  source_urls: string[];
  status: CardStatus;
  deleted_at: string | null;
  created_at: string;
  client_request_id: string;
}

export interface CardListResponse {
  items: CardRow[];
}

export interface ActiveCardExistsDetail {
  code: 'active_card_exists';
  message: string;
  existing_card_id: string;
  existing_card_name: string;
  existing_card_last_four: string | null;
}

export function isActiveCardExistsError(err: unknown): err is ApiError & {
  body: { detail: ActiveCardExistsDetail };
} {
  if (!(err instanceof ApiError) || err.status !== 409) return false;
  const detail = (err.body as { detail?: unknown } | null)?.detail;
  return (
    detail !== null &&
    typeof detail === 'object' &&
    'code' in detail &&
    (detail as { code: unknown }).code === 'active_card_exists'
  );
}

export async function lookupCard(name: string): Promise<CardLookupResponse> {
  return apiJson<CardLookupResponse>('/cards/lookup', {
    method: 'POST',
    body: { name },
  });
}

export async function confirmCard(proposal: CardProposal): Promise<CardRow> {
  return apiJson<CardRow>('/cards/confirm', {
    method: 'POST',
    body: proposal,
  });
}

export async function listCards(opts?: { includeInactive?: boolean }): Promise<CardListResponse> {
  const qs = opts?.includeInactive ? '?include_inactive=true' : '';
  return apiJson<CardListResponse>(`/cards${qs}`, { method: 'GET' });
}

export async function patchCard(
  cardId: string,
  patch: Partial<{
    name: string;
    program: CardProgram;
    multipliers: Record<string, number>;
    annual_fee: string | null;
    color: string | null;
  }>,
): Promise<CardRow> {
  return apiJson<CardRow>(`/cards/${cardId}`, {
    method: 'PATCH',
    body: patch,
  });
}

export async function deleteCard(cardId: string): Promise<void> {
  // Soft-delete on the server: status='deleted' + deleted_at=now(). Returns
  // 204 No Content, so `apiJson` (which JSON-parses) would throw; we use
  // `apiFetch` for the empty-body case and just check status.
  const response = await apiFetch(`/cards/${cardId}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new ApiError(response.status, null, `DELETE /cards/${cardId} failed`);
  }
}
