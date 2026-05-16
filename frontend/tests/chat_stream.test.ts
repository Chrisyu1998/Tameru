/**
 * SSE parser test for `frontend/src/lib/chat_stream.ts` — Day 12.
 *
 * Mocks `fetch` to return a Response whose body is a ReadableStream we
 * drive frame-by-frame. Covers:
 *   - happy path: token frames stream into onToken, tool_use into
 *     onToolUse, done into onDone.
 *   - multi-line data fields are re-joined with `\n`.
 *   - mid-stream `error` frame fires onError exactly once and no onDone.
 *   - clean stream with no terminal frame surfaces STREAM_INCOMPLETE.
 *   - chunked frame splits across reads still parse correctly.
 *   - HTTP 401 with a FastAPI-shaped {detail: {code, message}} body
 *     surfaces via onError with the structured code.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { streamTurn, type StreamDonePayload, type StreamToolUsePayload, type StreamError } from '@/lib/chat_stream';
import { useAppStore } from '@/store';

// Encode a sequence of byte chunks as a ReadableStream<Uint8Array>.
// One chunk per `enqueue` call so multi-chunk parsing is exercised
// when the test passes more than one chunk.
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
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

function mockFetchOk(chunks: string[]): void {
  globalThis.fetch = vi.fn(async () => {
    return new Response(streamFromChunks(chunks), {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }) as unknown as typeof fetch;
}

function mockFetchHttpError(status: number, body: unknown): void {
  globalThis.fetch = vi.fn(async () => {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
}

function captureCallbacks() {
  /** Aggregates the four callbacks plus the events that fired, in order. */
  const tokens: string[] = [];
  const toolUses: StreamToolUsePayload[] = [];
  const dones: StreamDonePayload[] = [];
  const errors: StreamError[] = [];
  return {
    tokens,
    toolUses,
    dones,
    errors,
    onToken: (text: string) => {
      tokens.push(text);
    },
    onToolUse: (payload: StreamToolUsePayload) => {
      toolUses.push(payload);
    },
    onDone: (payload: StreamDonePayload) => {
      dones.push(payload);
    },
    onError: (err: StreamError) => {
      errors.push(err);
    },
  };
}

describe('streamTurn — SSE parser', () => {
  beforeEach(() => {
    // Seed a JWT + deviceId so chat_stream's header builder doesn't
    // emit a bare request.
    useAppStore.setState({ jwt: 'test-jwt', deviceId: 'test-device' });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('emits token, tool_use, done in order', async () => {
    mockFetchOk([
      'event: token\ndata: Sure\n\n',
      'event: token\ndata:  thing.\n\n',
      'event: tool_use\ndata: {"name":"calculate_total","input":{"category":"Dining"}}\n\n',
      'event: done\ndata: {"conversation_id":"abc","tool_calls":[{"name":"calculate_total","input":{"category":"Dining"},"result":{"total":42,"count":1}}]}\n\n',
    ]);
    const cb = captureCallbacks();
    await streamTurn({
      message: 'how much on dining?',
      conversationId: null,
      ...cb,
    });
    expect(cb.tokens).toEqual(['Sure', ' thing.']);
    expect(cb.toolUses).toEqual([
      { name: 'calculate_total', input: { category: 'Dining' } },
    ]);
    expect(cb.dones).toHaveLength(1);
    expect(cb.dones[0].conversation_id).toBe('abc');
    expect(cb.dones[0].tool_calls).toEqual([
      {
        name: 'calculate_total',
        input: { category: 'Dining' },
        result: { total: 42, count: 1 },
      },
    ]);
    expect(cb.errors).toEqual([]);
  });

  test('multi-line data fields are re-joined with newlines', async () => {
    mockFetchOk([
      'event: token\ndata: line one\ndata: line two\n\n',
      'event: done\ndata: {"conversation_id":"abc","tool_calls":[]}\n\n',
    ]);
    const cb = captureCallbacks();
    await streamTurn({
      message: 'multiline',
      conversationId: null,
      ...cb,
    });
    expect(cb.tokens).toEqual(['line one\nline two']);
    expect(cb.dones).toHaveLength(1);
    expect(cb.errors).toEqual([]);
  });

  test('chunked frame splits across reads still parse correctly', async () => {
    // The first chunk ends mid-frame, the second completes it. The
    // parser must hold the partial in its buffer.
    mockFetchOk([
      'event: token\ndata: Hello',
      ' World\n\nevent: done\ndata: {"conversation_id":"x","tool_calls":[]}\n\n',
    ]);
    const cb = captureCallbacks();
    await streamTurn({
      message: 'split',
      conversationId: null,
      ...cb,
    });
    expect(cb.tokens).toEqual(['Hello World']);
    expect(cb.dones).toHaveLength(1);
    expect(cb.errors).toEqual([]);
  });

  test('mid-stream error frame fires onError, no onDone', async () => {
    mockFetchOk([
      'event: token\ndata: You spent $4\n\n',
      'event: error\ndata: {"code":"LOOP_LIMIT","message":"too many hops"}\n\n',
    ]);
    const cb = captureCallbacks();
    await streamTurn({
      message: 'bad',
      conversationId: null,
      ...cb,
    });
    expect(cb.tokens).toEqual(['You spent $4']);
    expect(cb.errors).toEqual([
      { code: 'LOOP_LIMIT', message: 'too many hops' },
    ]);
    expect(cb.dones).toEqual([]);
  });

  test('clean stream without a terminal frame surfaces STREAM_INCOMPLETE', async () => {
    mockFetchOk(['event: token\ndata: half\n\n']);
    const cb = captureCallbacks();
    await streamTurn({
      message: 'half',
      conversationId: null,
      ...cb,
    });
    expect(cb.tokens).toEqual(['half']);
    expect(cb.dones).toEqual([]);
    expect(cb.errors).toHaveLength(1);
    expect(cb.errors[0].code).toBe('STREAM_INCOMPLETE');
  });

  test('HTTP 401 surfaces structured code via onError', async () => {
    mockFetchHttpError(401, {
      detail: { code: 'DEVICE_DISPLACED', message: 'session ended' },
    });
    const cb = captureCallbacks();
    await streamTurn({
      message: 'displaced',
      conversationId: null,
      ...cb,
    });
    expect(cb.errors).toEqual([
      {
        code: 'DEVICE_DISPLACED',
        message: 'session ended',
        status: 401,
      },
    ]);
    expect(cb.tokens).toEqual([]);
    expect(cb.dones).toEqual([]);
    // The global displacement latch should be engaged for the modal.
    expect(useAppStore.getState().displaced).toBe(true);
  });

  test('CRLF line endings parse the same as LF', async () => {
    mockFetchOk([
      'event: token\r\ndata: hi\r\n\r\nevent: done\r\ndata: {"conversation_id":"a","tool_calls":[]}\r\n\r\n',
    ]);
    const cb = captureCallbacks();
    await streamTurn({
      message: 'crlf',
      conversationId: null,
      ...cb,
    });
    expect(cb.tokens).toEqual(['hi']);
    expect(cb.dones).toHaveLength(1);
    expect(cb.errors).toEqual([]);
  });
});
