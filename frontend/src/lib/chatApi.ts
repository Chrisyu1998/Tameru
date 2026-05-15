import { apiJson, ApiError } from './api';

/*
 * Typed wrapper for POST /chat/turn (app/routes/chat.py).
 *
 * The server runs the full Claude tool-use loop synchronously, persists both
 * the human-visible message + the wire-shape trace, and returns:
 *   - conversation_id: stable id; the client passes it on the next turn so
 *     history is loaded server-side (last 5 turns per DESIGN.md §7.2.1).
 *   - assistant_text: the final-iteration prose the user sees.
 *   - tool_calls: every tool the loop ran this turn, in order, with input
 *     args + return value. Used to render parse cards (propose_transaction)
 *     and tool-attribution chips (get_transactions, calculate_total, etc.).
 *
 * Errors observed in v1 (see chat.py):
 *   429 UCAP_EXCEEDED         — daily token cap hit (UX frame 16 amber card)
 *   503 PROVIDER_RATE_LIMITED — Anthropic rate-limited us; offer retry
 *   500 LOOP_LIMIT            — tool-use loop ran past safety cap; partial
 *                               turn not persisted
 * Any of these come back as ApiError with `body.detail.code` set.
 */

export interface ChatToolCall {
  name: string;
  input: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface ChatTurnResponse {
  conversation_id: string;
  assistant_text: string;
  tool_calls: ChatToolCall[];
}

export type ChatTurnErrorCode =
  | 'UCAP_EXCEEDED'
  | 'PROVIDER_RATE_LIMITED'
  | 'LOOP_LIMIT'
  | 'UNKNOWN';

export interface ChatTurnError {
  code: ChatTurnErrorCode;
  message: string;
  status: number;
}

export async function postChatTurn(
  message: string,
  conversationId: string | null,
): Promise<ChatTurnResponse> {
  return apiJson<ChatTurnResponse>('/chat/turn', {
    method: 'POST',
    body: {
      message,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    },
  });
}

export interface ChatMessageWire {
  role: 'user' | 'assistant';
  content_blocks: Array<{ type: string; text?: string; [k: string]: unknown }>;
  created_at: string;
}

export interface ChatMessagesResponse {
  messages: ChatMessageWire[];
  has_more: boolean;
}

export async function getChatMessages(
  conversationId: string,
): Promise<ChatMessagesResponse> {
  return apiJson<ChatMessagesResponse>(
    `/chat/messages?conversation_id=${encodeURIComponent(conversationId)}`,
  );
}

/**
 * Normalize a thrown ApiError into the structured shape the chat store
 * dispatches on. Non-ApiError throws (network failures, unexpected JSON,
 * etc.) become UNKNOWN with the original message.
 */
export function toChatTurnError(err: unknown): ChatTurnError {
  if (err instanceof ApiError) {
    const detail =
      err.body && typeof err.body === 'object' && 'detail' in (err.body as object)
        ? (err.body as { detail: unknown }).detail
        : err.body;
    let code: ChatTurnErrorCode = 'UNKNOWN';
    let message = err.message;
    if (detail && typeof detail === 'object') {
      const obj = detail as { code?: unknown; message?: unknown };
      if (
        obj.code === 'UCAP_EXCEEDED' ||
        obj.code === 'PROVIDER_RATE_LIMITED' ||
        obj.code === 'LOOP_LIMIT'
      ) {
        code = obj.code;
      }
      if (typeof obj.message === 'string') message = obj.message;
    }
    return { code, message, status: err.status };
  }
  return {
    code: 'UNKNOWN',
    message: err instanceof Error ? err.message : 'chat request failed',
    status: 0,
  };
}
