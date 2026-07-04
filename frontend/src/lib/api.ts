import { useAppStore } from '../store';

/*
 * Fetch wrapper for the Tameru backend.
 *
 * Cross-origin in production (frontend on Vercel, API on Railway — DESIGN.md §5.3).
 * Auth is Bearer token in Authorization header; we never send cookies, so
 * credentials are explicitly omitted. FastAPI's CORSMiddleware allowlists
 * the frontend origin (DESIGN.md §9.3) and accepts Authorization + X-Device-Id.
 *
 * Day 7: every authenticated route except /me and /auth/* runs through the
 * single-active-device gate. A 401 with `detail.code === 'DEVICE_DISPLACED'`
 * (or 'MISSING_DEVICE_ID') signals the user's session was displaced by a
 * sign-in on another browser; we latch the store flag here so the modal
 * pops globally rather than each caller having to handle it.
 */

const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

type FetchOptions = Omit<RequestInit, 'headers' | 'body'> & {
  headers?: Record<string, string>;
  body?: unknown;
};

function buildHeaders(extra?: Record<string, string>): Record<string, string> {
  const { jwt, deviceId } = useAppStore.getState();
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(extra ?? {}),
  };
  if (jwt) headers.Authorization = `Bearer ${jwt}`;
  if (deviceId) headers['X-Device-Id'] = deviceId;
  return headers;
}

export async function apiFetch(
  path: string,
  options: FetchOptions = {},
): Promise<Response> {
  const { headers, body, ...rest } = options;
  const resolvedHeaders = buildHeaders(headers);

  let encodedBody: BodyInit | undefined;
  if (body !== undefined) {
    if (body instanceof FormData || body instanceof Blob) {
      encodedBody = body;
    } else {
      resolvedHeaders['Content-Type'] = 'application/json';
      encodedBody = JSON.stringify(body);
    }
  }

  return fetch(`${API_URL}${path}`, {
    ...rest,
    headers: resolvedHeaders,
    body: encodedBody,
    credentials: 'omit',
  });
}

export function maybeFlagDisplacement(status: number, body: unknown): void {
  if (status !== 401) return;
  // FastAPI puts our structured error under `detail`. We accept either the
  // wrapped or raw form so a future move to a non-FastAPI shape doesn't
  // silently bypass the latch.
  const detail =
    body && typeof body === 'object' && 'detail' in (body as object)
      ? (body as { detail: unknown }).detail
      : body;
  const code =
    detail && typeof detail === 'object' && 'code' in (detail as object)
      ? (detail as { code: unknown }).code
      : undefined;
  if (code === 'DEVICE_DISPLACED' || code === 'MISSING_DEVICE_ID') {
    useAppStore.getState().setDisplaced(true);
  }
}

export async function apiJson<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const response = await apiFetch(path, options);
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    maybeFlagDisplacement(response.status, parsed);
    throw new ApiError(
      response.status,
      parsed,
      `API ${response.status} ${response.statusText} for ${path}`,
    );
  }
  return parsed as T;
}

export const apiBaseUrl = API_URL;
