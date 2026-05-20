/**
 * SSE parser test for `frontend/src/lib/imports_stream.ts` — Day 20.
 *
 * Mirrors the structure of `chat_stream.test.ts`. Mocks `fetch` to
 * return a Response whose body is a ReadableStream we drive frame by
 * frame. Covers:
 *   - happy path: progress frames stream into onProgress, done into onDone
 *   - mid-stream `error` frame fires onError exactly once
 *   - HTTP 422 (e.g. tampered token) surfaces via onError with the
 *     structured `{detail: {code, message}}` shape
 *   - stream ends without a terminal frame → STREAM_INCOMPLETE
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { commitCsv, type ImportStreamError } from '@/lib/imports_stream';
import type { CsvCommitDone, CsvCommitProgress } from '@/lib/importsApi';
import { useAppStore } from '@/store';

describe('commitCsv — SSE parser', () => {
  beforeEach(() => {
    useAppStore.setState({
      jwt: 'test-jwt',
      deviceId: 'test-device',
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ jwt: null, deviceId: null, displaced: false });
  });

  test('streams progress and done frames', async () => {
    /** Two progress frames + one done frame land in their callbacks. */
    const frames = [
      'event: progress\ndata: {"processed":1,"total":3,"current_category":"Dining"}\n\n',
      'event: progress\ndata: {"processed":2,"total":3,"current_category":"Gas"}\n\n',
      'event: done\ndata: {"done":true,"inserted":2,"skipped_duplicates":1,"skipped_refunds":0,"skipped_foreign_currency":0,"skipped_parse_errors":0}\n\n',
    ];
    _mockFetchOk(frames);

    const cb = _captureCallbacks();
    await commitCsv({
      file: new File(['x'], 'x.csv', { type: 'text/csv' }),
      cardId: 'card-1',
      importToken: 'token',
      columnMapping: { date: 'd', merchant: 'm', amount: 'a' },
      ...cb,
    });

    expect(cb.progress).toHaveLength(2);
    expect(cb.progress[0]).toEqual({
      processed: 1,
      total: 3,
      current_category: 'Dining',
    });
    expect(cb.dones).toHaveLength(1);
    expect(cb.dones[0]).toEqual({
      done: true,
      inserted: 2,
      skipped_duplicates: 1,
      skipped_refunds: 0,
      skipped_foreign_currency: 0,
      skipped_parse_errors: 0,
    });
    expect(cb.errors).toHaveLength(0);
  });

  test('mid-stream error frame surfaces via onError', async () => {
    /** An error frame fires onError exactly once; no done callback. */
    const frames = [
      'event: progress\ndata: {"processed":1,"total":5,"current_category":"Dining"}\n\n',
      'event: error\ndata: {"code":"rate_limited","message":"Gemini 429"}\n\n',
    ];
    _mockFetchOk(frames);

    const cb = _captureCallbacks();
    await commitCsv({
      file: new File(['x'], 'x.csv'),
      cardId: 'card-1',
      importToken: 'token',
      columnMapping: {},
      ...cb,
    });

    expect(cb.errors).toHaveLength(1);
    expect(cb.errors[0].code).toBe('rate_limited');
    expect(cb.errors[0].message).toBe('Gemini 429');
    expect(cb.dones).toHaveLength(0);
  });

  test('HTTP 422 with FastAPI {detail: {code,message}} body surfaces via onError', async () => {
    /** Preview-token mismatch returns 422 before the stream opens. */
    globalThis.fetch = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          detail: {
            code: 'invalid_import_token',
            message: 'uploaded file does not match the file from /preview',
          },
        }),
        { status: 422, headers: { 'Content-Type': 'application/json' } },
      );
    }) as unknown as typeof fetch;

    const cb = _captureCallbacks();
    await commitCsv({
      file: new File(['x'], 'x.csv'),
      cardId: 'card-1',
      importToken: 'bad-token',
      columnMapping: {},
      ...cb,
    });

    expect(cb.errors).toHaveLength(1);
    expect(cb.errors[0].code).toBe('invalid_import_token');
    expect(cb.errors[0].status).toBe(422);
  });

  test('stream that ends before a terminal frame surfaces STREAM_INCOMPLETE', async () => {
    /** Only progress frames arrive; no done/error → STREAM_INCOMPLETE. */
    const frames = [
      'event: progress\ndata: {"processed":1,"total":2,"current_category":"Dining"}\n\n',
    ];
    _mockFetchOk(frames);

    const cb = _captureCallbacks();
    await commitCsv({
      file: new File(['x'], 'x.csv'),
      cardId: 'card-1',
      importToken: 'token',
      columnMapping: {},
      ...cb,
    });

    expect(cb.errors).toHaveLength(1);
    expect(cb.errors[0].code).toBe('STREAM_INCOMPLETE');
    expect(cb.dones).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

function _streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  /** Build a ReadableStream that emits one chunk per `enqueue`. */
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

function _mockFetchOk(chunks: string[]): void {
  /** Install a fetch mock returning a 200 stream with the given chunks. */
  globalThis.fetch = vi.fn(async () => {
    return new Response(_streamFromChunks(chunks), {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }) as unknown as typeof fetch;
}

function _captureCallbacks() {
  /** Collect callback events for assertion in test bodies. */
  const progress: CsvCommitProgress[] = [];
  const dones: CsvCommitDone[] = [];
  const errors: ImportStreamError[] = [];
  return {
    progress,
    dones,
    errors,
    onProgress: (p: CsvCommitProgress) => {
      progress.push(p);
    },
    onDone: (d: CsvCommitDone) => {
      dones.push(d);
    },
    onError: (e: ImportStreamError) => {
      errors.push(e);
    },
  };
}
