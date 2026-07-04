/**
 * importsApi.previewCsv — displaced-device 401 latching.
 *
 * previewCsv uses the custom multipart path (not apiJson), so — like the
 * receipt-upload path — it must apply `maybeFlagDisplacement` itself. (The CSV
 * *commit* stream in imports_stream.ts already handled this; preview didn't,
 * an inconsistency this closes.)
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { previewCsv } from '@/lib/importsApi';
import { ApiError } from '@/lib/api';
import { useAppStore } from '@/store';

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'x',
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function csv(): File {
  return new File([new Uint8Array([1, 2, 3])], 'statement.csv', {
    type: 'text/csv',
  });
}

describe('previewCsv — displaced-device 401', () => {
  beforeEach(() => {
    useAppStore.getState().setDisplaced(false);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useAppStore.getState().setDisplaced(false);
  });

  test('a DEVICE_DISPLACED 401 latches the global displaced flag', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(401, { detail: { code: 'DEVICE_DISPLACED' } })),
    );

    await expect(previewCsv(csv(), 'card-1')).rejects.toBeInstanceOf(ApiError);
    expect(useAppStore.getState().displaced).toBe(true);
  });

  test('a plain 422 does NOT latch displacement', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(422, { detail: { code: 'empty_csv' } })),
    );

    await expect(previewCsv(csv(), 'card-1')).rejects.toBeInstanceOf(ApiError);
    expect(useAppStore.getState().displaced).toBe(false);
  });
});
