/*
 * Client for GET /export (Day 27, DESIGN.md §9.6).
 *
 * The backend returns a single JSON object with a `Content-Disposition:
 * attachment` header. This wrapper grabs the response as a Blob and
 * uses an off-DOM anchor click to trigger the browser's Save-As. No
 * Supabase Storage, no signed URL, no token — the user's Bearer JWT is
 * the auth, and the file streams in-memory.
 *
 * The endpoint is small at v1 scale (~10 users, weeks of data); if a
 * single export ever grows beyond a few MB this path will start
 * holding meaningful memory on the device. The right migration at that
 * point is server-side paginated dumps, not a different download
 * mechanism.
 */

import { apiFetch, ApiError } from './api';

export interface DownloadExportResult {
  filename: string;
  sizeBytes: number;
}

/**
 * Download the user's data export. Resolves with the filename + size
 * the browser actually saved; throws ApiError on a non-2xx response so
 * callers can surface error UI consistently with the rest of the app.
 */
export async function downloadUserDataExport(): Promise<DownloadExportResult> {
  const response = await apiFetch('/export', { method: 'GET' });
  if (!response.ok) {
    const bodyText = await response.text();
    let parsed: unknown = bodyText;
    try {
      parsed = bodyText ? JSON.parse(bodyText) : null;
    } catch {
      // Body wasn't JSON — keep the text form. Some failure paths
      // (e.g. a 502 from the edge) return HTML.
    }
    throw new ApiError(
      response.status,
      parsed,
      `GET /export failed: ${response.status} ${response.statusText}`,
    );
  }

  const blob = await response.blob();
  const filename = extractFilename(response) ?? defaultFilename();
  triggerDownload(blob, filename);
  return { filename, sizeBytes: blob.size };
}

/**
 * Pull the filename out of `Content-Disposition` if the server sent one.
 * Tolerates both `filename="x.json"` and `filename=x.json` shapes.
 */
function extractFilename(response: Response): string | null {
  const cd = response.headers.get('content-disposition');
  if (!cd) return null;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  return match ? match[1] : null;
}

/** Today as ISO date — fallback when the server omits a filename. */
function defaultFilename(): string {
  return `tameru-export-${new Date().toISOString().slice(0, 10)}.json`;
}

/**
 * Synthesize an off-DOM anchor click to save the blob. Standard PWA
 * download pattern: create an object URL, click an anchor with the
 * `download` attribute, revoke the URL so the blob doesn't leak.
 */
function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  // The anchor doesn't need to be in the DOM tree to fire `click`;
  // Safari historically required it but modern WebKit honors detached
  // anchors. Keeping it detached avoids any chance of CSS layout shift.
  anchor.click();
  // setTimeout(0) so the click event has flushed before the URL is
  // revoked — revoking too early can cancel the download on some
  // browsers.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
