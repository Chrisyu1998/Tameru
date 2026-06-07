import { apiFetch, apiJson, ApiError } from './api';

/*
 * Day 14 — typed client for /cards/* endpoints.
 *
 * Mirrors app/models/cards.py exactly. The shapes here are wire shapes —
 * if the backend Pydantic model changes, change this file in the same
 * commit. Components consume the types via `import type { ... } from
 * '@/lib/cardsApi'` to avoid duplicating the field set.
 */

// Tier 3 (DESIGN.md §6.6) added `jcb` + `diners`. Mirrors `CardNetwork`
// in app/models/cards.py + the `cards_network_check` CHECK.
export type CardNetwork =
  | 'visa'
  | 'mastercard'
  | 'amex'
  | 'discover'
  | 'jcb'
  | 'diners'
  | 'other';
export type CardProgram = 'UR' | 'MR' | 'TYP' | 'Bilt' | 'Other';

// Per-card region — drives reward-lookup routing. Mirrors `CardRegion` in
// app/models/cards.py + the `cards_region_check` CHECK (Tier 3).
export type CardRegion = 'US' | 'JP' | 'TW';

// Closed-enum issuer. Mirrors `app/models/cards.py::CardIssuer`, the DB
// CHECK constraint (migrations 20260516140000 + 20260602120000), and the
// `card_issuers` reference table. If you add a value here, update the
// backend Literal + DB CHECK + card_issuers seed in the same change.
export type CardIssuer =
  // US
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
  // JP
  | 'rakuten'
  | 'smbc'
  | 'jcb'
  | 'aeon'
  | 'epos'
  | 'saison'
  // TW
  | 'cathay'
  | 'esun'
  | 'ctbc'
  | 'taishin'
  | 'fubon'
  | 'union'
  | 'other';

// Friendly labels for the UI — the enum stays canonical snake_case for the
// wire / DB; the display map exists so chips and selects show titlecase
// without sprinkling lookup code through components. Mirrors
// `card_issuers.display_name`.
export const ISSUER_LABELS: Record<CardIssuer, string> = {
  // US
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
  // JP
  rakuten: 'Rakuten',
  smbc: 'SMBC',
  jcb: 'JCB',
  aeon: 'AEON',
  epos: 'Epos',
  saison: 'Saison',
  // TW
  cathay: 'Cathay United',
  esun: 'E.SUN',
  ctbc: 'CTBC',
  taishin: 'Taishin',
  fubon: 'Fubon',
  union: 'Union Bank',
  other: 'Other',
};

export const ISSUERS: readonly CardIssuer[] = Object.keys(
  ISSUER_LABELS,
) as CardIssuer[];

// Which issuers belong to which region — mirrors `card_issuers.region`.
// Used by the add-card UI to filter the issuer picker to the selected
// region. `other` is intentionally absent (no fixed region).
export const ISSUER_REGION: Partial<Record<CardIssuer, CardRegion>> = {
  chase: 'US',
  amex: 'US',
  citi: 'US',
  capital_one: 'US',
  discover: 'US',
  bank_of_america: 'US',
  wells_fargo: 'US',
  usaa: 'US',
  bilt: 'US',
  barclays: 'US',
  us_bank: 'US',
  synchrony: 'US',
  rakuten: 'JP',
  smbc: 'JP',
  jcb: 'JP',
  aeon: 'JP',
  epos: 'JP',
  saison: 'JP',
  cathay: 'TW',
  esun: 'TW',
  ctbc: 'TW',
  taishin: 'TW',
  fubon: 'TW',
  union: 'TW',
};

// home_currency → default region for the add-card region selector.
// Only the two non-US currencies that map to a supported region need
// entries; everything else defaults to US.
export function regionForCurrency(homeCurrency: string | null): CardRegion {
  if (homeCurrency === 'JPY') return 'JP';
  if (homeCurrency === 'TWD') return 'TW';
  return 'US';
}

export interface CardLookupResult {
  program: CardProgram | null;
  network: CardNetwork | null;
  multipliers: Record<string, number>;
  // Tier 3 (DESIGN.md §6.6) — the JP/TW base-rate shape. Filled instead of
  // `multipliers` on non-US cards.
  base_reward_rate?: string | null;
  rewards_currency?: string | null;
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
  // Tier 3 (DESIGN.md §6.6) — JP/TW base-rate reward shape, carried from
  // the lookup to `/cards/confirm`.
  base_reward_rate?: string | null;
  rewards_currency?: string | null;
  // The region the user picked on the add-card form. The confirm route only
  // honors it for an unenumerated (`other`) issuer — a known issuer's region
  // is server-pinned. Omit/null on the chat path (no picker); confirm then
  // falls back to the home-currency guess.
  region?: CardRegion | null;
  annual_fee?: string | null;
  /**
   * Day 19b — optional renewal date for AF tracking. When set alongside
   * a non-zero `annual_fee`, `POST /cards/confirm` creates a companion
   * subscriptions row so the pg_cron auto-logger logs the AF on each
   * anniversary. ISO date (YYYY-MM-DD) or null. Must be >= today.
   */
  next_annual_fee_date?: string | null;
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
  // Tier 3 (DESIGN.md §6.6). `region` is NOT NULL (default 'US' on the DB);
  // base-rate fields are null on US cards.
  region: CardRegion;
  base_reward_rate: string | null;
  rewards_currency: string | null;
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

export async function lookupCard(
  name: string,
  region?: CardRegion,
): Promise<CardLookupResponse> {
  // `region` is optional — when omitted the backend derives it from the
  // user's home_currency. The add-card UI passes the selector value so a
  // US-card-in-a-TWD-wallet add can be routed to US sources (Tier 3).
  return apiJson<CardLookupResponse>('/cards/lookup', {
    method: 'POST',
    body: region ? { name, region } : { name },
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

/**
 * `next_annual_fee_date` is a virtual patch field — there's no
 * `cards.next_annual_fee_date` column. When present, the backend's
 * `PATCH /cards/{id}` routes through the `update_card_af` RPC and
 * cascades to the companion AF subscription's `next_billing_date`
 * (Day 19b, DESIGN.md §6.5). Set to `null` to stop AF tracking
 * (cancels the companion subscription); the cards row's `annual_fee`
 * snapshot is preserved. Set to a date on a card whose AF tracking
 * was previously cancelled to re-enable.
 */
export interface CardPatchBody {
  name?: string;
  program?: CardProgram;
  multipliers?: Record<string, number>;
  annual_fee?: string | null;
  color?: string | null;
  next_annual_fee_date?: string | null;
}

export async function patchCard(
  cardId: string,
  patch: CardPatchBody,
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
