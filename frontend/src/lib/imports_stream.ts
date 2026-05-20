/**
 * SSE client for POST /imports/csv/commit — Day 20.
 *
 * Mirrors the shape of `chat_stream.ts`. The backend route returns
 * `Content-Type: text/event-stream` with three frame kinds:
 *   - event: progress  data: {processed, total, current_category}
 *   - event: done      data: {inserted, skipped_*, ...}
 *   - event: error     data: {code, message}
 *
 * EventSource can't send Authorization headers in any browser, so we
 * use fetch + ReadableStream + a manual SSE parser. The two streams
 * share the same wire framing and the same `done`/`error` terminal
 * conventions; the only differences are the per-frame payload shapes
 * and that there's no idempotent retry contract (the backend writes
 * rows during the stream, so re-running the import relies on the
 * dedup quadruple to recover — DESIGN.md §5.4.3).
 */

import { useAppStore } from '../store';
import { apiBaseUrl } from './api';
import type { CsvCommitDone, CsvCommitProgress } from './importsApi';

export type ImportStreamErrorCode =
  | 'rate_limited'
  | 'provider_error'
  | 'timeout'
  | 'json_parse_error'
  | 'schema_violation'
  | 'insert_failed'
  | 'unknown'
  | 'DEVICE_DISPLACED'
  | 'MISSING_DEVICE_ID'
  | 'NETWORK'
  | 'ABORTED'
  | 'STREAM_INCOMPLETE'
  | 'PARSE_ERROR';

export interface ImportStreamError {
  code: ImportStreamErrorCode | string;
  message: string;
  status?: number;
}

export interface CommitCsvOptions {
  file: File;
  cardId: string;
  importToken: string;
  columnMapping: object;
  onProgress: (payload: CsvCommitProgress) => void;
  onDone: (payload: CsvCommitDone) => void;
  onError: (err: ImportStreamError) => void;
  signal?: AbortSignal;
}

export async function commitCsv(opts: CommitCsvOptions): Promise<void> {
  /**
   * Open the SSE stream and drive callbacks until terminal `done` /
   * `error` / network drop. Resolves once the stream finishes (cleanly
   * or otherwise). The caller awaits this to know when to unset its
   * own busy flag.
   */
  const { jwt, deviceId } = useAppStore.getState();
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
  };
  if (jwt) headers.Authorization = `Bearer ${jwt}`;
  if (deviceId) headers['X-Device-Id'] = deviceId;

  const form = new FormData();
  form.append('file', opts.file);
  form.append('card_id', opts.cardId);
  form.append('import_token', opts.importToken);
  form.append('column_mapping', JSON.stringify(opts.columnMapping));

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/imports/csv/commit`, {
      method: 'POST',
      headers,
      body: form,
      credentials: 'omit',
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      opts.onError({ code: 'ABORTED', message: 'request was aborted' });
    } else {
      opts.onError({
        code: 'NETWORK',
        message: err instanceof Error ? err.message : 'network error',
      });
    }
    return;
  }

  if (!response.ok) {
    let parsed: unknown = null;
    try {
      const text = await response.text();
      parsed = text ? JSON.parse(text) : null;
    } catch {
      // non-JSON body
    }
    const { code, message } = extractStructuredError(parsed, response.status);
    if (
      response.status === 401 &&
      (code === 'DEVICE_DISPLACED' || code === 'MISSING_DEVICE_ID')
    ) {
      useAppStore.getState().setDisplaced(true);
    }
    opts.onError({ code, message, status: response.status });
    return;
  }

  if (!response.body) {
    opts.onError({
      code: 'STREAM_INCOMPLETE',
      message: 'response has no body',
    });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let seenTerminal = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

      let idx: number;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const terminal = dispatchFrame(frame, opts);
        if (terminal) seenTerminal = true;
      }
    }

    if (buffer.trim().length > 0) {
      const terminal = dispatchFrame(buffer, opts);
      if (terminal) seenTerminal = true;
    }

    if (!seenTerminal) {
      opts.onError({
        code: 'STREAM_INCOMPLETE',
        message: 'stream ended before done or error frame',
      });
    }
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      opts.onError({ code: 'ABORTED', message: 'request was aborted' });
    } else {
      opts.onError({
        code: 'NETWORK',
        message: err instanceof Error ? err.message : 'stream read failed',
      });
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // already released — fine
    }
  }
}

// ---------------------------------------------------------------------------
// Internals.
// ---------------------------------------------------------------------------

function dispatchFrame(frame: string, opts: CommitCsvOptions): boolean {
  /**
   * Parse one SSE frame and dispatch to the appropriate callback.
   * Returns true iff the frame is terminal (`done` or `error`).
   */
  let eventName = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue;
    if (line.startsWith('event:')) {
      eventName = line.slice('event:'.length).replace(/^ /, '').trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''));
    }
  }
  const data = dataLines.join('\n');

  switch (eventName) {
    case 'progress': {
      const parsed = tryParseJson(data);
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as Partial<CsvCommitProgress>;
        if (
          typeof obj.processed === 'number' &&
          typeof obj.total === 'number' &&
          typeof obj.current_category === 'string'
        ) {
          opts.onProgress({
            processed: obj.processed,
            total: obj.total,
            current_category: obj.current_category,
          });
        }
      }
      return false;
    }
    case 'done': {
      const parsed = tryParseJson(data);
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as Partial<CsvCommitDone>;
        if (
          typeof obj.inserted === 'number' &&
          typeof obj.skipped_duplicates === 'number' &&
          typeof obj.skipped_refunds === 'number' &&
          typeof obj.skipped_foreign_currency === 'number' &&
          typeof obj.skipped_parse_errors === 'number'
        ) {
          opts.onDone({
            done: true,
            inserted: obj.inserted,
            skipped_duplicates: obj.skipped_duplicates,
            skipped_refunds: obj.skipped_refunds,
            skipped_foreign_currency: obj.skipped_foreign_currency,
            skipped_parse_errors: obj.skipped_parse_errors,
          });
          return true;
        }
      }
      opts.onError({ code: 'PARSE_ERROR', message: 'malformed done frame' });
      return true;
    }
    case 'error': {
      const parsed = tryParseJson(data);
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as { code?: unknown; message?: unknown };
        opts.onError({
          code: typeof obj.code === 'string' ? obj.code : 'unknown',
          message: typeof obj.message === 'string' ? obj.message : data,
        });
      } else {
        opts.onError({ code: 'unknown', message: data || 'stream error' });
      }
      return true;
    }
    default:
      // Unknown event name — ignore for forward compat.
      return false;
  }
}

function tryParseJson(text: string): unknown {
  /** Best-effort JSON parse for an SSE data payload. */
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function extractStructuredError(
  body: unknown,
  status: number,
): { code: string; message: string } {
  /**
   * Pull `{code, message}` from the FastAPI `{detail: {...}}` shape
   * (or the bare `{code, message}` shape), fall back to HTTP_<status>.
   */
  const detail =
    body && typeof body === 'object' && 'detail' in (body as object)
      ? (body as { detail: unknown }).detail
      : body;
  if (detail && typeof detail === 'object') {
    const obj = detail as { code?: unknown; message?: unknown };
    const code = typeof obj.code === 'string' ? obj.code : `HTTP_${status}`;
    const message =
      typeof obj.message === 'string' ? obj.message : `request failed (${status})`;
    return { code, message };
  }
  return { code: `HTTP_${status}`, message: `request failed (${status})` };
}
