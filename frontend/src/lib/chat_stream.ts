/**
 * SSE client for POST /chat/turn — Day 12.
 *
 * The route returns `Content-Type: text/event-stream` with four frame
 * kinds:
 *   - event: token     data: <text chunk>          (one per text delta)
 *   - event: tool_use  data: {"name", "input"}     (one per tool call)
 *   - event: done      data: {"conversation_id", "tool_calls"}
 *   - event: error     data: {"code", "message"}
 *
 * EventSource can't send Authorization headers in any browser, so we use
 * fetch + ReadableStream + a small manual SSE parser. The `done.tool_calls`
 * payload is byte-for-byte the same shape Day 8 returned in JSON, so
 * Day 10's ParseCard / CandidateList consume it unchanged via `onDone`.
 *
 * Failure surfaces (everything routes through `onError`):
 *   - HTTP error opening the stream  → status + code from response body
 *   - Mid-stream `error` SSE frame   → code/message from the frame
 *   - Network drop / abort           → code: 'NETWORK' or 'ABORTED'
 *   - Stream ends without a `done`   → code: 'STREAM_INCOMPLETE'
 *
 * The caller decides what to render. There is no retry built in — the
 * chat UI re-fires the original message with the same conversation_id.
 * The backend writes neither chat_messages nor chat_turn_trace until
 * `done` fires, so retry is idempotent (DESIGN.md §7.5).
 */

import { useAppStore } from '../store';
import { apiBaseUrl } from './api';
import type { ChatToolCall } from './chatApi';

export interface StreamToolUsePayload {
  name: string;
  input: Record<string, unknown>;
}

export interface StreamDonePayload {
  conversation_id: string;
  tool_calls: ChatToolCall[];
}

export type StreamErrorCode =
  | 'DAILY_CAP_EXCEEDED'
  | 'AI_PROVIDER_RATE_LIMITED'
  | 'LOOP_LIMIT'
  | 'PERSISTENCE_FAILED'
  | 'DEVICE_DISPLACED'
  | 'MISSING_DEVICE_ID'
  | 'NETWORK'
  | 'ABORTED'
  | 'STREAM_INCOMPLETE'
  | 'PARSE_ERROR'
  | 'UNKNOWN';

export interface StreamError {
  code: StreamErrorCode | string;
  message: string;
  status?: number;
}

export interface StreamTurnOptions {
  message: string;
  conversationId: string | null;
  onToken: (text: string) => void;
  onToolUse: (payload: StreamToolUsePayload) => void;
  onDone: (payload: StreamDonePayload) => void;
  onError: (err: StreamError) => void;
  signal?: AbortSignal;
}

export async function streamTurn(opts: StreamTurnOptions): Promise<void> {
  /**
   * Open the SSE stream and drive the four callbacks until terminal
   * `done` / `error` / network drop. Resolves once the stream finishes
   * (cleanly or otherwise). The caller awaits this to know when to
   * unset the `busy` flag.
   */
  const { jwt, deviceId } = useAppStore.getState();
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  if (jwt) headers.Authorization = `Bearer ${jwt}`;
  if (deviceId) headers['X-Device-Id'] = deviceId;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/chat/turn`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        message: opts.message,
        ...(opts.conversationId ? { conversation_id: opts.conversationId } : {}),
      }),
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
    // 401 / 422 / 5xx with a JSON body before the stream opens. Try to
    // parse the structured `{detail: {code, message}}` FastAPI shape so
    // the caller can treat e.g. DEVICE_DISPLACED uniformly. Fall back
    // to a generic UNKNOWN with the status if the body isn't JSON.
    let parsed: unknown = null;
    try {
      const text = await response.text();
      parsed = text ? JSON.parse(text) : null;
    } catch {
      // non-JSON body
    }
    const { code, message } = extractStructuredError(parsed, response.status);
    // Match api.ts's global 401 latch: a DEVICE_DISPLACED / MISSING_DEVICE_ID
    // response should pop the displacement modal regardless of caller.
    // chat_stream bypasses apiFetch, so we replicate the latch inline.
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
    // Some browsers / proxies strip the body on certain content types;
    // surface this as a stream incomplete so the UI shows the retry
    // affordance rather than wedging in a 'thinking…' state.
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
      // SSE allows \r, \n, or \r\n line endings. Normalize so frame
      // splitting on a single delimiter works regardless of which one
      // the server (or any intermediate proxy) emits.
      buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

      let idx: number;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const terminal = dispatchFrame(frame, opts);
        if (terminal) {
          seenTerminal = true;
          // No early break here — the server already closed the stream
          // after `done`/`error`, but if anything extra arrives we want
          // it surfaced through the parser, not silently ignored.
        }
      }
    }

    // Flush a trailing frame that didn't end in a blank line. SSE
    // implementations sometimes omit the final terminator on graceful
    // close; we try to recover one last frame from the buffer.
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
    // Best-effort: release the reader's lock so the underlying stream
    // can be garbage-collected. Errors here are not actionable.
    try {
      reader.releaseLock();
    } catch {
      // already released or stream torn down — fine.
    }
  }
}

// ---------------------------------------------------------------------------
// Internals.
// ---------------------------------------------------------------------------

/**
 * Parse one SSE frame and dispatch to the appropriate callback. Returns
 * true iff the frame is terminal (`done` or `error`), so the caller can
 * record that a clean termination happened.
 *
 * Multi-line `data:` fields are concatenated with `\n` per the SSE spec.
 * The leading space after `data:` is optional per spec but conventional
 * — we strip exactly one if present so `data: hello` and `data:hello`
 * round-trip to the same payload.
 */
function dispatchFrame(frame: string, opts: StreamTurnOptions): boolean {
  let eventName = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) {
      // SSE comment line — ignore.
      continue;
    }
    if (line.startsWith('event:')) {
      eventName = line.slice('event:'.length).replace(/^ /, '').trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''));
    }
  }
  const data = dataLines.join('\n');

  switch (eventName) {
    case 'token':
      opts.onToken(data);
      return false;
    case 'tool_use': {
      const parsed = tryParseJson(data);
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as { name?: unknown; input?: unknown };
        opts.onToolUse({
          name: typeof obj.name === 'string' ? obj.name : '',
          input:
            obj.input && typeof obj.input === 'object'
              ? (obj.input as Record<string, unknown>)
              : {},
        });
      }
      return false;
    }
    case 'done': {
      const parsed = tryParseJson(data);
      if (
        parsed &&
        typeof parsed === 'object' &&
        typeof (parsed as { conversation_id?: unknown }).conversation_id ===
          'string'
      ) {
        const obj = parsed as {
          conversation_id: string;
          tool_calls?: unknown;
        };
        opts.onDone({
          conversation_id: obj.conversation_id,
          tool_calls: Array.isArray(obj.tool_calls)
            ? (obj.tool_calls as ChatToolCall[])
            : [],
        });
      } else {
        opts.onError({
          code: 'PARSE_ERROR',
          message: 'malformed done frame',
        });
      }
      return true;
    }
    case 'error': {
      const parsed = tryParseJson(data);
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as { code?: unknown; message?: unknown };
        opts.onError({
          code: typeof obj.code === 'string' ? obj.code : 'UNKNOWN',
          message: typeof obj.message === 'string' ? obj.message : data,
        });
      } else {
        opts.onError({ code: 'UNKNOWN', message: data || 'stream error' });
      }
      return true;
    }
    default:
      // Unknown event name — silently ignore. Forward-compat in case
      // the backend grows new event types we don't recognize yet.
      return false;
  }
}

function tryParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Pull `{code, message}` out of a FastAPI `{detail: {...}}` body, or
 * the bare `{code, message}` shape, or fall back to a generic UNKNOWN.
 * The DEVICE_DISPLACED branch is intentionally left for the chatStore
 * to act on — surfacing the code verbatim is enough; the global 401
 * latch lives in api.ts and we don't dual-handle it here.
 */
function extractStructuredError(
  body: unknown,
  status: number,
): { code: string; message: string } {
  const detail =
    body && typeof body === 'object' && 'detail' in (body as object)
      ? (body as { detail: unknown }).detail
      : body;
  if (detail && typeof detail === 'object') {
    const obj = detail as { code?: unknown; message?: unknown };
    const code = typeof obj.code === 'string' ? obj.code : `HTTP_${status}`;
    const message =
      typeof obj.message === 'string'
        ? obj.message
        : `chat stream request failed (HTTP ${status})`;
    return { code, message };
  }
  return {
    code: status === 401 ? 'UNAUTHORIZED' : `HTTP_${status}`,
    message: `chat stream request failed (HTTP ${status})`,
  };
}
