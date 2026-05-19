/*
 * Day 19 — typed client for /subscriptions/* endpoints.
 *
 * Mirrors app/models/subscriptions.py exactly. If the backend Pydantic
 * model changes, change this file in the same commit.
 *
 * `card_id` is nullable — cardless subscriptions (bank ACH bills like
 * rent or utilities) are first-class (DESIGN.md §8.3).
 * `frequency` and `start_date` are immutable post-create — the PATCH
 * body shape below omits them.
 */

import { apiFetch, apiJson, ApiError } from "./api";

export type Frequency = "monthly" | "quarterly" | "annual" | "weekly";
export type SubscriptionStatus = "active" | "paused" | "cancelled";

export interface SubscriptionProposal {
  name: string;
  amount: string;
  frequency: Frequency;
  start_date: string;
  next_billing_date: string;
  category: string;
  card_id: string | null;
  /**
   * Stable per-proposal idempotency token, minted server-side at
   * `propose_subscription` time. The frontend posts it back verbatim at
   * `/subscriptions/confirm`; the partial unique index on
   * `subscriptions (user_id, client_request_id)` makes a same-crid
   * replay return the existing row rather than inserting a duplicate
   * (DESIGN.md §8.3, Day 15 offline-queue drain).
   */
  client_request_id: string;
}

export interface SubscriptionRow {
  id: string;
  user_id: string;
  card_id: string | null;
  name: string;
  amount: string;
  frequency: Frequency;
  start_date: string;
  next_billing_date: string;
  category: string;
  status: SubscriptionStatus;
  client_request_id: string | null;
  created_at: string;
}

export interface SubscriptionListResponse {
  items: SubscriptionRow[];
}

/**
 * PATCH body. Omits `frequency` and `start_date` per the §8.3
 * immutability rule — the backend rejects them with 422 anyway (model
 * has `extra='forbid'`).
 */
export interface SubscriptionPatchBody {
  name?: string;
  amount?: string;
  category?: string;
  card_id?: string | null;
  status?: SubscriptionStatus;
}

export async function confirmSubscription(
  proposal: SubscriptionProposal,
): Promise<SubscriptionRow> {
  return apiJson<SubscriptionRow>("/subscriptions/confirm", {
    method: "POST",
    body: proposal,
  });
}

/**
 * List subscriptions for the signed-in user.
 *
 * `status` filters by lifecycle ('active' by default; 'all' merges
 * every status). `includeCardAf` defaults to false — card annual-fee
 * companion subscriptions are hidden from the standard list because
 * they're conceptually a card consequence, not a user-tracked
 * subscription (DESIGN.md §6.5). The cards-list AF chip is the only
 * surface that should pass `includeCardAf: true`.
 */
export async function listSubscriptions(
  status: SubscriptionStatus | "all" = "active",
  options: { includeCardAf?: boolean } = {},
): Promise<SubscriptionListResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (options.includeCardAf) params.set("include_card_af", "true");
  const qs = params.toString();
  return apiJson<SubscriptionListResponse>(
    `/subscriptions${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
}

export async function patchSubscription(
  subscriptionId: string,
  patch: SubscriptionPatchBody,
): Promise<SubscriptionRow> {
  return apiJson<SubscriptionRow>(`/subscriptions/${subscriptionId}`, {
    method: "PATCH",
    body: patch,
  });
}

export async function deleteSubscription(
  subscriptionId: string,
): Promise<void> {
  const response = await apiFetch(`/subscriptions/${subscriptionId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      null,
      `DELETE /subscriptions/${subscriptionId} failed`,
    );
  }
}
