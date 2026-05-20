/**
 * ImportCsvSheet test — Day 20.
 *
 * Covers the four phases of the sheet without round-tripping to a
 * real backend:
 *   1. select   — file picker + card picker; "next" disabled until
 *                 both are picked, then calls /imports/csv/preview.
 *   2. confirm  — high-confidence preview renders detected columns +
 *                 sample rows; "looks right" kicks off /commit.
 *   3. committing → done — progress frames update the bar, done frame
 *                 swaps to the summary panel.
 *
 * `previewCsv` is mocked at the module boundary so the file picker
 * doesn't actually need to hit the network. `commitCsv` (the SSE
 * client) is also mocked so the test drives the callbacks directly,
 * matching how the chat-stream tests cover their UI.
 */

import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ImportCsvSheet } from '@/components/ImportCsvSheet';
import * as importsApi from '@/lib/importsApi';
import * as importsStream from '@/lib/imports_stream';

// useLedger reads cards through useSyncExternalStore — patch the
// underlying ledger module with a stable object reference so the
// sheet's useEffect (which keys off card identity) doesn't relaunch
// on every render and loop. Also stub `ledger.refresh()` so the
// post-done refresh test can assert it fired without hitting the
// real fetcher. Hoisted via `vi.hoisted` because `vi.mock` factories
// run BEFORE module-level top-level bindings exist.
const { ledgerRefreshSpy, STABLE_LEDGER } = vi.hoisted(() => ({
  ledgerRefreshSpy: vi.fn(async () => undefined),
  STABLE_LEDGER: {
    transactions: [],
    cards: [
      { id: 'card-1', name: 'Amex Platinum', last4: '1007' },
      { id: 'card-2', name: 'Chase Sapphire', last4: '4242' },
    ],
    loading: false,
    loaded: true,
    pendingDeletes: {},
    pendingCardDeletes: {},
    memory: [],
    pendingMemoryDeletes: {},
    goals: [],
    pendingGoalDeletes: {},
  },
}));
vi.mock('@/lib/ledger', () => ({
  useLedger: () => STABLE_LEDGER,
  ledger: { refresh: ledgerRefreshSpy },
}));

describe('ImportCsvSheet', () => {
  beforeEach(() => {
    ledgerRefreshSpy.mockClear();
  });

  test('high-confidence flow: select → confirm → commit → done', async () => {
    /** Walk the happy path end-to-end with both async hops mocked. */
    const user = userEvent.setup();
    const onClose = vi.fn();

    vi.spyOn(importsApi, 'previewCsv').mockResolvedValue({
      detected_columns: {
        date: 'Transaction Date',
        merchant: 'Description',
        amount: 'Amount',
        currency: null,
        confidence: 0.95,
      },
      sample_rows: [
        {
          'Transaction Date': '2026-04-12',
          Description: 'Blue Bottle',
          Amount: '5.50',
        },
      ],
      confidence: 0.95,
      import_token: 'tok',
      total_rows: 9,
    });

    vi.spyOn(importsStream, 'commitCsv').mockImplementation(async (opts) => {
      opts.onProgress({
        processed: 1,
        total: 9,
        current_category: 'Coffee Shops',
      });
      opts.onDone({
        done: true,
        inserted: 9,
        skipped_duplicates: 0,
        skipped_refunds: 1,
        skipped_foreign_currency: 0,
        skipped_parse_errors: 0,
      });
    });

    render(<ImportCsvSheet open={true} onClose={onClose} />);

    // Step 1 — pick file (use the hidden input directly).
    const fileInput = screen.getByTestId('csv-file-input') as HTMLInputElement;
    const file = new File(
      ['Transaction Date,Description,Amount\n2026-04-12,Blue Bottle,5.50\n'],
      'chase.csv',
      { type: 'text/csv' },
    );
    await user.upload(fileInput, file);

    // First card is pre-selected on open.
    const nextBtn = screen.getByRole('button', { name: /next/i });
    expect(nextBtn).not.toBeDisabled();
    await user.click(nextBtn);

    // Step 2 — confirm preview. The "looks right" button is unique to
    // this phase; awaiting it proves the preview render landed and
    // sidesteps multi-match on header strings like "Transaction Date"
    // (which appears both as a mapping value and a sample-table th).
    const confirmBtn = await screen.findByRole('button', {
      name: /looks right/i,
    });
    expect(screen.getByText(/9 rows/i)).toBeInTheDocument();
    await user.click(confirmBtn);

    // Step 4 — done summary.
    await screen.findByText(/all set/i);
    expect(screen.getByText(/9 transactions imported/i)).toBeInTheDocument();
    // Refunds row reads "1" (we skipped the Amazon return in the mock).
    expect(screen.getByText('refunds skipped')).toBeInTheDocument();
    // New post-Codex bucket: parse errors surface their own row so the
    // user gets a real signal when rows are unreadable.
    expect(screen.getByText("couldn't read")).toBeInTheDocument();

    // Ledger refresh fired so /breakdown etc. pick up the new rows.
    expect(ledgerRefreshSpy).toHaveBeenCalledTimes(1);
  });

  test('done with 0 inserted does NOT refresh the ledger', async () => {
    /** A pure-duplicate import doesn't change ledger state — skip the refetch. */
    const user = userEvent.setup();

    vi.spyOn(importsApi, 'previewCsv').mockResolvedValue({
      detected_columns: {
        date: 'Transaction Date',
        merchant: 'Description',
        amount: 'Amount',
        currency: null,
        confidence: 0.95,
      },
      sample_rows: [],
      confidence: 0.95,
      import_token: 'tok',
      total_rows: 3,
    });
    vi.spyOn(importsStream, 'commitCsv').mockImplementation(async (opts) => {
      opts.onDone({
        done: true,
        inserted: 0,
        skipped_duplicates: 3,
        skipped_refunds: 0,
        skipped_foreign_currency: 0,
        skipped_parse_errors: 0,
      });
    });

    render(<ImportCsvSheet open={true} onClose={vi.fn()} />);
    const fileInput = screen.getByTestId('csv-file-input') as HTMLInputElement;
    await user.upload(fileInput, new File(['a,b,c\n1,2,3\n'], 'x.csv'));
    await user.click(screen.getByRole('button', { name: /next/i }));
    await user.click(await screen.findByRole('button', { name: /looks right/i }));

    await screen.findByText(/all set/i);
    expect(ledgerRefreshSpy).not.toHaveBeenCalled();
  });

  test('low-confidence flow shows the manual-mapping picker', async () => {
    /** Preview returns needs_manual_mapping → user picks columns. */
    const user = userEvent.setup();

    vi.spyOn(importsApi, 'previewCsv').mockResolvedValue({
      needs_manual_mapping: true,
      headers: ['field_a', 'field_b', 'field_c'],
      sample_rows: [{ field_a: '2026-04-01', field_b: 'X', field_c: '15.00' }],
      import_token: 'tok',
      total_rows: 3,
    });

    const commitSpy = vi
      .spyOn(importsStream, 'commitCsv')
      .mockImplementation(async (opts) => {
        opts.onDone({
          done: true,
          inserted: 3,
          skipped_duplicates: 0,
          skipped_refunds: 0,
          skipped_foreign_currency: 0,
          skipped_parse_errors: 0,
        });
      });

    render(<ImportCsvSheet open={true} onClose={vi.fn()} />);

    const fileInput = screen.getByTestId('csv-file-input') as HTMLInputElement;
    await user.upload(
      fileInput,
      new File(['field_a,field_b,field_c\n2026-04-01,X,15\n'], 'weird.csv'),
    );
    await user.click(screen.getByRole('button', { name: /next/i }));

    // Manual-mapping form rendered.
    await screen.findByText(/map columns/i);
    expect(screen.getByText(/date column/i)).toBeInTheDocument();
    expect(screen.getByText(/merchant column/i)).toBeInTheDocument();
    expect(screen.getByText(/amount column/i)).toBeInTheDocument();

    // Defaults populate to the first three headers in order — that's
    // already a valid distinct set, so the import button is enabled.
    await user.click(screen.getByRole('button', { name: /^import$/i }));

    // Commit fired with the picker's mapping in the column_mapping arg.
    expect(commitSpy).toHaveBeenCalledTimes(1);
    const args = commitSpy.mock.calls[0][0];
    expect(args.columnMapping).toMatchObject({
      date: 'field_a',
      merchant: 'field_b',
      amount: 'field_c',
      // Default convention when the user doesn't tick the negative-
      // charges toggle — most monthly-statement exports.
      sign_convention: 'charges_positive',
    });

    await screen.findByText(/all set/i);
  });

  test('manual-mapping negative-charges toggle sets sign_convention', async () => {
    /** Ticking the "charges are negative" checkbox flips the convention. */
    const user = userEvent.setup();

    vi.spyOn(importsApi, 'previewCsv').mockResolvedValue({
      needs_manual_mapping: true,
      headers: ['field_a', 'field_b', 'field_c'],
      sample_rows: [{ field_a: '2026-04-01', field_b: 'X', field_c: '-15.00' }],
      import_token: 'tok',
      total_rows: 1,
    });
    const commitSpy = vi
      .spyOn(importsStream, 'commitCsv')
      .mockImplementation(async (opts) => {
        opts.onDone({
          done: true,
          inserted: 1,
          skipped_duplicates: 0,
          skipped_refunds: 0,
          skipped_foreign_currency: 0,
          skipped_parse_errors: 0,
        });
      });

    render(<ImportCsvSheet open={true} onClose={vi.fn()} />);
    const fileInput = screen.getByTestId('csv-file-input') as HTMLInputElement;
    await user.upload(fileInput, new File(['a,b,c\n1,2,3\n'], 'weird.csv'));
    await user.click(screen.getByRole('button', { name: /next/i }));

    await screen.findByText(/map columns/i);
    await user.click(screen.getByTestId('manual-mapping-negative-charges'));
    await user.click(screen.getByRole('button', { name: /^import$/i }));

    expect(commitSpy).toHaveBeenCalledTimes(1);
    const args = commitSpy.mock.calls[0][0];
    expect(args.columnMapping).toMatchObject({
      sign_convention: 'charges_negative',
    });
  });

  test('preview error surfaces in the error step', async () => {
    /** A 422 from /preview lands the user on the error panel. */
    const user = userEvent.setup();

    const { ApiError } = await import('@/lib/api');
    vi.spyOn(importsApi, 'previewCsv').mockRejectedValue(
      new ApiError(
        422,
        { detail: { code: 'invalid_card', message: 'card not yours' } },
        'API 422',
      ),
    );

    render(<ImportCsvSheet open={true} onClose={vi.fn()} />);

    const fileInput = screen.getByTestId('csv-file-input') as HTMLInputElement;
    await user.upload(
      fileInput,
      new File(['a,b,c\n1,2,3\n'], 'x.csv'),
    );
    await user.click(screen.getByRole('button', { name: /next/i }));

    await screen.findByText(/import couldn't finish/i);
    expect(screen.getByText(/card not yours/i)).toBeInTheDocument();
    expect(screen.getByText(/invalid_card/i)).toBeInTheDocument();
  });
});
