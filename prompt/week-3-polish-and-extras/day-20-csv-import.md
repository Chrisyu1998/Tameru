# Day 20 — CSV bank import (Gemini column detect + batch categorize + dedup)

## Goal

Upload a bank/card CSV. Gemini identifies date/merchant/amount columns; user confirms; rows batch-categorize in groups of 100; duplicates are flagged.

## Read first

- `DESIGN.md` §5.4.3 (CSV import), §6 (feature row "CSV bank import"), §8.2 (transactions — `source = "csv_import"`).

## Deliverables

- `app/routes/imports.py`:
  - `POST /imports/csv/preview` — multipart upload + `card_id`. Gemini reads the header row + first 5 data rows; returns `{detected_columns: {date, merchant, amount}, sample_rows: [...], confidence}`. If confidence < 0.8, return `{needs_manual_mapping: true, headers: [...]}` instead.
  - `POST /imports/csv/commit` — body: `{import_id, column_mapping, card_id}`. Streams the CSV, batches rows in groups of 100, calls Gemini batch-categorize, inserts via `transactions` with `source = "csv_import"`. Returns SSE stream of progress events: `{processed, total, current_category}` and a final `{done: true, inserted, skipped_duplicates}`.
- `app/integrations/gemini.py`:
  - Add `detect_columns(headers, sample_rows) -> ColumnMapping` — single Gemini call.
  - Add `categorize_batch(rows: list[(merchant, amount)], past_corrections) -> list[CategorySuggestion]` — one Gemini call per 100 rows.
- Duplicate detection in `commit`:
  - For each candidate row, check `transactions` for any existing row with same `(user_id, date, merchant_normalized, amount)`. If found, skip and add to `skipped_duplicates` count. Surface count in the final SSE event so the UI can show "143 imported, 7 duplicates skipped."
- `tests/test_csv_import.py`:
  - Sample CSVs in `tests/fixtures/csv/` for Chase, Amex, BofA. (Make these up — 10–20 rows each.)
  - Preview returns the right columns with confidence > 0.8 for known formats.
  - Commit inserts rows correctly; re-running the same import inserts 0 new rows (all flagged as duplicates).
  - Manual mapping path: upload a CSV with weird headers, assert the response asks for mapping.

## Don't

- Don't write CSV parsing yourself — use Python's stdlib `csv` module.
- Don't store the uploaded file beyond the request lifecycle. Process and discard.
- Don't categorize in a single 1000-row Gemini call. Batches of 100, with a backoff on 429.

## Done when

- `pytest tests/test_csv_import.py` passes.
- Curling a real Chase CSV through `/preview` then `/commit` populates `transactions` with `source = "csv_import"` and the SSE stream emits progress events.
