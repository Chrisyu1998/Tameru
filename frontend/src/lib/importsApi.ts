/**
 * Day 20 — typed client for /imports/csv/* endpoints.
 *
 * Mirrors `app/models/imports.py` exactly. The shapes here are wire
 * shapes — if the backend Pydantic model changes, change this file in
 * the same commit. The SSE consumer for `/commit` lives in its own
 * module (`imports_stream.ts`) because it has a different return shape
 * (callbacks, not a Promise<T>).
 */

import { apiFetch, ApiError, apiBaseUrl, maybeFlagDisplacement } from './api';
import { useAppStore } from '../store';

export type SignConvention = 'charges_positive' | 'charges_negative';

export interface ColumnMapping {
  date: string;
  merchant: string;
  amount: string;
  currency: string | null;
  /**
   * How the issuer encodes charges vs. credits in the amount column.
   * `charges_positive` (default) — Amex/Discover/most statement
   * exports: purchases positive, refunds/payments negative.
   * `charges_negative` — Chase/Citi activity exports: purchases
   * negative, refunds/payments positive. The route normalizes amounts
   * to the `charges_positive` posture before refund-skip and dedup,
   * so downstream code stays issuer-agnostic.
   */
  sign_convention?: SignConvention;
  confidence: number;
}

export interface ColumnPreview {
  detected_columns: ColumnMapping;
  sample_rows: Record<string, string>[];
  confidence: number;
  import_token: string;
  total_rows: number;
  /** Tag used by `isColumnPreview` for the union discrimination. */
  needs_manual_mapping?: undefined;
}

export interface ManualMappingPreview {
  needs_manual_mapping: true;
  headers: string[];
  sample_rows: Record<string, string>[];
  import_token: string;
  total_rows: number;
}

export type PreviewResponse = ColumnPreview | ManualMappingPreview;

export function isManualMapping(
  resp: PreviewResponse,
): resp is ManualMappingPreview {
  return resp.needs_manual_mapping === true;
}

export interface CsvCommitProgress {
  processed: number;
  total: number;
  current_category: string;
}

export interface CsvCommitDone {
  done: true;
  inserted: number;
  skipped_duplicates: number;
  skipped_refunds: number;
  skipped_foreign_currency: number;
  skipped_parse_errors: number;
}

/**
 * `POST /imports/csv/preview` — multipart upload + card_id.
 *
 * Does NOT go through `apiFetch` because the body is a FormData with a
 * File field; we build the auth headers inline and let fetch decide the
 * Content-Type so the multipart boundary is correct.
 */
export async function previewCsv(
  file: File,
  cardId: string,
): Promise<PreviewResponse> {
  const { jwt, deviceId } = useAppStore.getState();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (jwt) headers.Authorization = `Bearer ${jwt}`;
  if (deviceId) headers['X-Device-Id'] = deviceId;

  const form = new FormData();
  form.append('file', file);
  form.append('card_id', cardId);

  const resp = await apiFetch('/imports/csv/preview', {
    method: 'POST',
    headers,
    body: form,
  });
  const text = await resp.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    // This multipart path bypasses apiJson, so latch a displaced-device 401
    // ourselves (matching the receipt-upload path) — otherwise a
    // DEVICE_DISPLACED / MISSING_DEVICE_ID on a CSV preview never pops the
    // global single-active-device modal.
    maybeFlagDisplacement(resp.status, parsed);
    throw new ApiError(
      resp.status,
      parsed,
      `API ${resp.status} ${resp.statusText} for /imports/csv/preview`,
    );
  }
  return parsed as PreviewResponse;
}

export const importsBaseUrl = apiBaseUrl;
