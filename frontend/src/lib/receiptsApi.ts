/**
 * Typed client for `POST /receipts/parse` (app/routes/receipts.py).
 *
 * A downscaled receipt JPEG is uploaded; the backend runs one Gemini Vision
 * call and returns a `TransactionProposal` (source='receipt_photo') — the same
 * wire shape `propose_transaction` returns. The chat store maps it to a
 * `ParseDraft` (reusing `_wireProposalToDraft`) and commits it via the existing
 * `POST /transactions/confirm`, so idempotency + the entry-moment insight are
 * inherited unchanged.
 *
 * Mirrors `previewCsv`: does NOT go through `apiJson`, because the body is a
 * `FormData` with an image field — auth headers are built inline and fetch is
 * left to set the multipart boundary.
 */

import { apiFetch, ApiError, maybeFlagDisplacement } from './api';
import { useAppStore } from '../store';
import type { Category } from './categories';

/**
 * Wire shape of the `TransactionProposal` the backend returns (mirrors
 * app/models/transactions.py). `source` distinguishes the chat path (`nlp`)
 * from the receipt path (`receipt_photo`); it round-trips to
 * `POST /transactions/confirm` so the committed row is attributed correctly.
 */
export interface TransactionProposalWire {
  merchant: string;
  amount: string | number;
  date: string;
  card_id: string | null;
  category: Category;
  notes: string | null;
  gemini_suggestion: string | null;
  client_request_id: string;
  source: 'nlp' | 'receipt_photo';
}

export async function parseReceipt(image: Blob): Promise<TransactionProposalWire> {
  const { jwt, deviceId } = useAppStore.getState();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (jwt) headers.Authorization = `Bearer ${jwt}`;
  if (deviceId) headers['X-Device-Id'] = deviceId;

  const form = new FormData();
  // The backend keys on the part's content-type (image/jpeg), but a real
  // filename keeps request logs readable.
  form.append('file', image, 'receipt.jpg');

  const resp = await apiFetch('/receipts/parse', {
    method: 'POST',
    headers,
    body: form,
  });
  const text = await resp.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    // This multipart path bypasses apiJson, so latch a displaced-device 401
    // ourselves — otherwise a DEVICE_DISPLACED / MISSING_DEVICE_ID on a receipt
    // upload never pops the global single-active-device modal (it'd surface as
    // a generic "couldn't scan that receipt" instead).
    maybeFlagDisplacement(resp.status, parsed);
    throw new ApiError(
      resp.status,
      parsed,
      `API ${resp.status} ${resp.statusText} for /receipts/parse`,
    );
  }
  return parsed as TransactionProposalWire;
}
