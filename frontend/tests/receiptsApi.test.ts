/**
 * receiptsApi.parseReceipt — displaced-device 401 latching.
 *
 * parseReceipt uses the custom multipart path (not apiJson), so it must apply
 * `maybeFlagDisplacement` itself. Without it, a DEVICE_DISPLACED 401 on a
 * receipt upload would surface as a generic scan error and never pop the global
 * single-active-device modal. These tests pin that the store's `displaced` flag
 * latches on such a 401 — and only on a displacement code, not other errors.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { parseReceipt } from '@/lib/receiptsApi';
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

function jpeg(): Blob {
  return new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' });
}

describe('parseReceipt — displaced-device 401', () => {
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

    await expect(parseReceipt(jpeg())).rejects.toBeInstanceOf(ApiError);
    expect(useAppStore.getState().displaced).toBe(true);
  });

  test('a plain 503 does NOT latch displacement', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(503, { detail: { code: 'provider_error' } })),
    );

    await expect(parseReceipt(jpeg())).rejects.toBeInstanceOf(ApiError);
    expect(useAppStore.getState().displaced).toBe(false);
  });
});
