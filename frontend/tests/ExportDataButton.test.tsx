/**
 * Day 27 — ExportDataButton wraps the `/export` JSON download.
 *
 * Covered states:
 *   - Idle → loading → idle on a successful download; analytics fires
 *     `feature_used` with feature='data_export'.
 *   - Error path: failure surfaces an inline alert, fires `error_shown`,
 *     keeps the button enabled for retry.
 *   - Concurrent-click guard: a second click while the first is in
 *     flight does nothing (no extra fetch, no double analytics).
 *
 * The library helper `downloadUserDataExport` owns the Blob/anchor
 * mechanics; that surface is mocked here to keep tests in the
 * component's behavioral space rather than the URL/anchor lifecycle.
 */

import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/exportApi', () => ({
  downloadUserDataExport: vi.fn(),
}));

vi.mock('@/lib/analytics', () => ({
  track: vi.fn(),
}));

import { ExportDataButton } from '@/components/ExportDataButton';
import { downloadUserDataExport } from '@/lib/exportApi';
import { track } from '@/lib/analytics';

const mockedDownload = vi.mocked(downloadUserDataExport);
const mockedTrack = vi.mocked(track);

beforeEach(() => {
  mockedDownload.mockReset();
  mockedTrack.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ExportDataButton', () => {
  test('fires feature_used and returns to idle on success', async () => {
    mockedDownload.mockResolvedValue({
      filename: 'tameru-export-2026-05-26.json',
      sizeBytes: 1234,
    });

    render(<ExportDataButton />);
    const button = screen.getByTestId('export-data-button');

    expect(button).toBeEnabled();
    expect(button).toHaveTextContent(/export/i);

    fireEvent.click(button);

    await waitFor(() => {
      expect(mockedTrack).toHaveBeenCalledWith('feature_used', {
        feature: 'data_export',
      });
    });

    expect(mockedDownload).toHaveBeenCalledTimes(1);
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent(/export/i);
    expect(screen.queryByTestId('export-data-error')).toBeNull();
  });

  test('shows the loading state while the download is in flight', async () => {
    let resolve!: () => void;
    mockedDownload.mockImplementation(
      () =>
        new Promise((res) => {
          resolve = () =>
            res({ filename: 'tameru-export-2026-05-26.json', sizeBytes: 1 });
        }),
    );

    render(<ExportDataButton />);
    const button = screen.getByTestId('export-data-button');

    fireEvent.click(button);

    // While the download is pending the button is disabled and shows
    // the preparing copy.
    await waitFor(() => {
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute('aria-busy', 'true');
      expect(button).toHaveTextContent(/preparing/i);
    });

    resolve();

    await waitFor(() => {
      expect(button).toBeEnabled();
      expect(button).toHaveTextContent(/export/i);
    });
  });

  test('surfaces an inline error and fires error_shown on failure', async () => {
    mockedDownload.mockRejectedValue(new Error('network'));

    render(<ExportDataButton />);
    const button = screen.getByTestId('export-data-button');

    fireEvent.click(button);

    const alert = await screen.findByTestId('export-data-error');
    expect(alert).toHaveTextContent(/couldn't prepare your export/i);
    expect(mockedTrack).toHaveBeenCalledWith('error_shown', {
      code: 'internal_error',
    });

    // Button should be re-enabled for retry, not stuck in the loading
    // state.
    expect(button).toBeEnabled();
  });

  test('ignores a second click while the first download is in flight', async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    mockedDownload.mockImplementation(
      () =>
        new Promise((res) => {
          resolve = () =>
            res({ filename: 'tameru-export-2026-05-26.json', sizeBytes: 1 });
        }),
    );

    render(<ExportDataButton />);
    const button = screen.getByTestId('export-data-button');

    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());

    // The button is disabled while loading; userEvent's click respects
    // pointer-events:none / disabled state and silently no-ops, so the
    // assertion below verifies the guard at the side-effect level.
    await user.click(button);

    expect(mockedDownload).toHaveBeenCalledTimes(1);

    resolve();
    await waitFor(() => expect(button).toBeEnabled());
  });
});
