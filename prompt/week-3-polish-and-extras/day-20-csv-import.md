# Day 20 — CSV bank import (Gemini column detect + batch categorize + dedup)

## Goal

Upload a bank/card CSV. Gemini identifies date/merchant/amount columns; user confirms; rows batch-categorize in groups of 100; duplicates flagged; non-importable rows (negative amounts, foreign currency) counted and surfaced. CSV is the named v1 exception to the chat-only write surface (CLAUDE.md invariant 8) — bulk/async, not a `tool_use` path.

Backend only for this prompt. The Settings → Import Data UI and the per-bank instructions screen (DESIGN.md §5.4.3) ship in a later prompt — this one delivers `/imports/csv/preview` + `/imports/csv/commit`, the supporting Gemini integration, and full `ai_call_log` coverage.

## Read first

- `DESIGN.md` §5.4.3 (CSV import — size caps, negative-skip, foreign-reject, idempotent re-run), §8.2 (`transactions` — `source = 'csv_import'`, `client_request_id = NULL` for CSV inserts), §8.8 (`ai_call_log` — `task_type = 'csv_import'`), §17.5 line 1520 (server-side CSV upload limits).
- `CLAUDE.md` invariants 1 (per-request Supabase client + user JWT), 8 (CSV is the named v1 exception), 14 (`ai_call_log` writes use the user JWT, not service role).
- [app/util/merchant.py](app/util/merchant.py) — `normalize_merchant`, the canonical normalizer reused at every transaction-write site ([app/routes/transactions.py:368](app/routes/transactions.py#L368), [app/integrations/gemini.py:116](app/integrations/gemini.py#L116)). The CSV dedup key reuses this.
- [app/integrations/gemini.py](app/integrations/gemini.py) — the per-call `ai_call_log` pattern (sentinel-initialized `model` + `prompt_hash`, single outer block covering preflight + SDK + parse). Copy this shape for `detect_columns` and `categorize_batch`.

## Decisions landed at review

- **Upload limits: 5 MB file size, 5,000 rows.** Covers ~2.5 years of typical US bank export with headroom. Rejected at the route boundary before any parsing (§17.5 line 1520). Easier to relax later than tighten.
- **Negative amounts (returns / credits) are skipped, not inserted.** Counted separately from duplicates. v1 over-counts gross spending for users who return things — acceptable at invite-only ~10-user scale; refund handling is Phase 2.
- **Foreign-currency rows are rejected.** If the detected schema includes a currency column and a row's currency code ≠ `users_meta.home_currency`, skip + count. If no currency column exists at all, trust the row is in home currency (every US bank export from Chase/Amex/BofA/Citi/Capital One/Wells Fargo is). Aligns with invariant 13 (single immutable home currency, no FX).
- **No recurring detection on import.** Same merchant appearing 12 times lands as 12 one-off `csv_import` transactions. Recurring detection is Phase 2 (DESIGN.md §6 line 289).
- **Disconnect handling: idempotent re-run, no resume endpoint.** The dedup quadruple `(user_id, date, normalize_merchant(merchant), amount)` is the de-facto resume key. A user re-uploading after a mid-stream disconnect sees "0 new, N duplicates skipped" for the part that already committed. No partial-import-status table to design, no resume cursor to migrate later.
- **Cross-call state via stateless HMAC, not server-side storage.** `/preview` returns an `import_token` (HMAC of `user_id + file_hash + detected_columns + expires_at`). `/commit` re-receives the file + the token and verifies. Honors "Don't store the uploaded file beyond the request lifecycle."
- **Dedup query targets the `active_transactions` view, not the base table.** A user who deleted a row should be able to re-import it (deletion shadows neither future inserts nor future imports).

## Deliverables

### `app/routes/imports.py` — new file

- **`POST /imports/csv/preview`** — multipart upload (`file`) + form `card_id`.
  - Reject `Content-Length` > 5 MB and `UploadFile.size` > 5 MB at the route boundary, before `.read()`. 413.
  - Parse with stdlib `csv.reader`; count rows. Reject > 5,000 rows. 413.
  - Read the header row + first 5 data rows; call `detect_columns(...)`.
  - Returns Pydantic `ColumnPreview`:
    ```json
    {
      "detected_columns": {"date": "Posting Date", "merchant": "Description", "amount": "Amount", "currency": null},
      "sample_rows": [{"Posting Date": "2026-04-12", "Description": "Blue Bottle Coffee", "Amount": "-5.50"}],
      "confidence": 0.94,
      "import_token": "<HMAC>.<base64-payload>",
      "total_rows": 247
    }
    ```
  - If `confidence < 0.8`, return `{needs_manual_mapping: true, headers: list[str], sample_rows, import_token, total_rows}` instead. UI presents a column picker; the user's mapping rides through to `/commit` as `column_mapping`.
  - Writes one `ai_call_log` row, `task_type = 'csv_import'`, under the caller's JWT.

- **`POST /imports/csv/commit`** — multipart upload (`file`) + form `{import_token, column_mapping (JSON), card_id}`.
  - Verify HMAC `import_token` (signs `user_id + file_hash + detected_columns + expires_at`, 15 min TTL). 422 on mismatch / expiry.
  - Re-validate size + row count (same caps as preview).
  - Validate `card_id` ownership against `cards` (mirrors Day 5's transaction-confirm check); 422 on miss.
  - Stream the CSV; for each batch of ≤100 rows:
    - Call `categorize_batch(rows, past_corrections, user)`.
    - For each row in the batch:
      - **Skip if `amount < 0`**, increment `skipped_refunds`.
      - **Skip if currency column present and value ≠ `users_meta.home_currency`**, increment `skipped_foreign_currency`.
      - **Dedup**: SELECT 1 FROM `active_transactions` WHERE `user_id = auth.uid() AND date = $1 AND normalize_merchant(merchant) = $2 AND amount = $3`. If found, increment `skipped_duplicates`. (Run the normalizer in Python on the candidate, compare against the stored already-normalized value — `transactions.merchant` is stored normalized today, see [transactions.py:368](app/routes/transactions.py#L368).)
      - Otherwise, INSERT into `transactions` with `source = 'csv_import'`, `client_request_id = NULL` (§8.2 line 834), `gemini_suggestion` = the batch result for this row.
  - SSE stream:
    - Per row: `{processed: int, total: int, current_category: str}`.
    - Final: `{done: true, inserted, skipped_duplicates, skipped_refunds, skipped_foreign_currency}`.
  - Each `categorize_batch` call writes one `ai_call_log` row, `task_type = 'csv_import'`, under the caller's JWT.

### `app/integrations/gemini.py` — additions

- **`detect_columns(headers: list[str], sample_rows: list[dict[str, str]], user: AuthedUser) -> ColumnMapping`** — one Gemini call. Returns Pydantic `ColumnMapping(date: str, merchant: str, amount: str, currency: str | None, confidence: float)`. Model resolved via existing `_model_name()` — **no hardcoded model string**. `ai_call_log` shape mirrors `categorize()` exactly (single outer block, sentinel-initialized `model` + `prompt_hash`, log fires whether the SDK call succeeded, failed in preflight, or failed in parse).

- **`categorize_batch(rows: list[tuple[str, float]], past_corrections: list[tuple[str, str]], user: AuthedUser) -> list[CategorySuggestion]`** — one Gemini call per batch of ≤100 rows. `past_corrections` shape matches `_read_past_corrections()` (most-recent-first list of `(merchant, category)` pairs, reused verbatim). Response is a list aligned 1:1 with input order. 429 backoff: exponential with jitter, max 3 retries; on exhaustion, raise so the route can return a 503 to the client. One `ai_call_log` row per call (not per row).

### Pydantic models

`app/models/imports.py` (or co-located in the route file if that's the existing convention): `ColumnPreview`, `ColumnMapping`, `ManualMappingPreview`, `CsvCommitProgress`, `CsvCommitDone`. Boundaries are typed per CLAUDE.md "Code organization doctrine."

### `tests/test_csv_import.py`

- Fixtures in `tests/fixtures/csv/`: hand-rolled `chase_sample.csv`, `amex_sample.csv`, `bofa_sample.csv` (10–20 rows each). Include at least one refund row (negative amount) and one fixture with a `Currency` column carrying a non-USD value. Add `weird_headers_sample.csv` for the manual-mapping path.
- **Mock Gemini** (`detect_columns`, `categorize_batch`) at the unit-test layer — non-deterministic LLM output must not gate CI. Confidence-threshold branching is verified by mocking high/low confidence responses.
- Coverage:
  - Preview parses Chase/Amex/BofA headers via mocked Gemini and returns the right `ColumnMapping`.
  - Manual-mapping path: low-confidence mock returns `{needs_manual_mapping: true, ...}`.
  - Commit inserts rows with `source = 'csv_import'`, `client_request_id IS NULL`.
  - Negative-amount rows skipped and surfaced as `skipped_refunds`.
  - Foreign-currency rows skipped and surfaced as `skipped_foreign_currency`.
  - Re-running the same import inserts 0 new rows; every row lands in `skipped_duplicates`.
  - `ai_call_log` row count: 1 per preview + `ceil(rows / 100)` per commit. All rows tagged `task_type = 'csv_import'`.
  - Upload-size reject: 6 MB file → 413; 5,001-row CSV → 413.
  - `import_token` tampering / expiry → 422.
- LLM-quality assertions ("does Gemini actually pick the right column on real Chase headers?") move to `evals/csv_import/` — not a unit-test target.

## Don't

- Don't write CSV parsing yourself — stdlib `csv` module.
- Don't store the uploaded file. Process in-request and discard. `import_token` is stateless HMAC, not a storage handle.
- Don't categorize in a single 1000-row Gemini call. Batches of ≤100 with 429 backoff.
- Don't hardcode the Gemini model — resolve via `_model_name()` / env vars (CLAUDE.md "Model usage by task").
- Don't insert negative-amount or foreign-currency rows. Skip + count + surface in the final SSE event.
- Don't set `client_request_id` on CSV inserts — leave NULL (§8.2 line 834). Idempotency comes from the dedup quadruple, not from crid.
- Don't query the base `transactions` table for dedup — query `active_transactions` so a deleted row doesn't shadow a re-import.
- Don't link CSV-imported rows to existing subscriptions — recurring detection is Phase 2.
- Don't write `ai_call_log` rows via the service role (CLAUDE.md invariant 14) — use the caller's JWT through `supabase_for_user(user.jwt)`.
- Don't ship a resume endpoint or partial-import status table. Re-upload is the recovery path.

## Done when

- `pytest tests/test_csv_import.py` passes (Gemini mocked).
- Curling a real Chase CSV through `/preview` then `/commit` populates `transactions` with `source = 'csv_import'`, `client_request_id IS NULL`, and the SSE stream emits per-row progress + a final event carrying all four counters.
- `ai_call_log` shows one row per Gemini call, all `task_type = 'csv_import'`, written under the caller's JWT (not service role).
- 6 MB upload → 413; 5,001-row CSV → 413.
- Re-importing the same file: SSE final event shows `inserted = 0`, every row in `skipped_duplicates`.
