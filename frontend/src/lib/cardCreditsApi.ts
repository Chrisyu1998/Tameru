import { apiFetch, apiJson, ApiError } from './api';

/*
 * Phase 1 — typed client for /card-credits/* (DESIGN.md §6.7, §8.17).
 *
 * Mirrors app/models/card_credits.py exactly. Wire shapes — if the backend
 * Pydantic model changes, change this file in the same commit.
 */

export type CreditCadence = 'monthly' | 'quarterly' | 'semiannual' | 'annual';
export type CreditStatus = 'active' | 'archived';

export const CREDIT_CADENCES: readonly CreditCadence[] = [
  'monthly',
  'quarterly',
  'semiannual',
  'annual',
];

// Dual-role: the lookup returns a list of these (server-minted crid), and the
// confirm accepts the same list back. A manual add builds one with a
// client-minted crid, empty source_urls, null verified_at.
export interface CreditProposal {
  card_id: string;
  name: string;
  amount: string | null;
  cadence: CreditCadence;
  merchant_hint: string | null;
  source_urls: string[];
  verified_at: string | null;
  client_request_id: string;
}

export interface CardCreditRow {
  id: string;
  user_id: string;
  card_id: string;
  name: string;
  amount: string | null;
  cadence: CreditCadence;
  used_amount: string;
  current_period_start: string;
  next_reset_date: string;
  merchant_hint: string | null;
  status: CreditStatus;
  source_urls: string[];
  verified_at: string | null;
  client_request_id: string;
  created_at: string;
}

export interface CardCreditsLookupResponse {
  card_id: string;
  card_name: string;
  credits: CreditProposal[];
  source_urls: string[];
  needs_manual: boolean;
}

export interface CardCreditListResponse {
  items: CardCreditRow[];
}

export interface CardCreditPatchBody {
  used_amount?: string | null;
  name?: string | null;
  amount?: string | null;
  cadence?: CreditCadence | null;
  status?: CreditStatus | null;
}

/** Run the web_search-backed credit-list lookup for a card. Never a hard error:
 * a miss returns `needs_manual=true` with an empty `credits` list. */
export async function lookupCredits(
  cardId: string,
): Promise<CardCreditsLookupResponse> {
  return apiJson<CardCreditsLookupResponse>('/card-credits/lookup', {
    method: 'POST',
    body: { card_id: cardId },
  });
}

/** Commit a checklist of proposals. Idempotent — a replay lands no new rows and
 * returns fewer items. Returns the rows that actually landed. */
export async function confirmCredits(
  credits: CreditProposal[],
): Promise<CardCreditListResponse> {
  return apiJson<CardCreditListResponse>('/card-credits/confirm', {
    method: 'POST',
    body: { credits },
  });
}

export async function listCredits(
  cardId: string,
  opts?: { includeArchived?: boolean },
): Promise<CardCreditListResponse> {
  const params = new URLSearchParams({ card_id: cardId });
  if (opts?.includeArchived) params.set('include_archived', 'true');
  return apiJson<CardCreditListResponse>(`/card-credits?${params.toString()}`, {
    method: 'GET',
  });
}

export async function patchCredit(
  creditId: string,
  patch: CardCreditPatchBody,
): Promise<CardCreditRow> {
  return apiJson<CardCreditRow>(`/card-credits/${creditId}`, {
    method: 'PATCH',
    body: patch,
  });
}

export async function deleteCredit(creditId: string): Promise<void> {
  const response = await apiFetch(`/card-credits/${creditId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      null,
      `DELETE /card-credits/${creditId} failed`,
    );
  }
}

/** Build a manual-add proposal (client-minted crid, no lookup provenance). */
export function manualCreditProposal(
  cardId: string,
  fields: { name: string; amount: string | null; cadence: CreditCadence },
): CreditProposal {
  return {
    card_id: cardId,
    name: fields.name,
    amount: fields.amount,
    cadence: fields.cadence,
    merchant_hint: null,
    source_urls: [],
    verified_at: null,
    client_request_id: crypto.randomUUID(),
  };
}
