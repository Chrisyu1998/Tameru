import { useAppStore } from '../store';

/*
 * Fetch wrapper for the Tameru backend.
 *
 * Cross-origin in production (frontend on Vercel, API on Railway — DESIGN.md §5.3).
 * Auth is Bearer token in Authorization header; we never send cookies, so
 * credentials are explicitly omitted. FastAPI's CORSMiddleware allowlists
 * the frontend origin (DESIGN.md §9.3) and accepts Authorization + X-Device-Id.
 *
 * TODO(Day 7): jwt and deviceId are populated from the Supabase session after
 * sign-in, and X-Device-Id header enforcement on the backend lands in Day 7
 * (single-active-device, DESIGN.md §9.1).
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

export async function apiJson<T>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const response = await apiFetch(path, options);
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      parsed,
      `API ${response.status} ${response.statusText} for ${path}`,
    );
  }
  return parsed as T;
}

export const apiBaseUrl = API_URL;
