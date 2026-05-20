"""CSV import wire shapes — Day 20.

The two endpoints (`/imports/csv/preview`, `/imports/csv/commit`) and the
two Gemini integration calls (`detect_columns`, `categorize_batch`) share
these types so the wire shape and the integration return shape cannot
drift.

`import_token` is a stateless HMAC threaded through preview→commit; we do
NOT store the uploaded file between calls. The client re-uploads the same
bytes to `/commit`, and the token's `file_hash` field is verified against
the re-uploaded payload (DESIGN.md §5.4.3 — "Idempotent re-run as the
recovery path", plus the Don't list in `prompt/week-3-polish-and-extras/
day-20-csv-import.md`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


SignConvention = Literal["charges_positive", "charges_negative"]


class ColumnMapping(BaseModel):
    """Detected column-name mapping returned by Gemini's preview parse.

    Values are header names from the user's CSV (e.g. `date='Posting Date'`
    for a Chase export). `currency` is None when no currency column is
    present in the schema — typical for single-currency US bank exports.

    `sign_convention` flags how the issuer encodes charges vs. credits in
    the amount column. Two families exist in the wild:

      * `charges_positive` (default) — Amex, Discover, most statement-style
        exports: a $50 purchase shows as `50.00`, a $30 refund as `-30.00`.
      * `charges_negative` — Chase activity export, Citi activity export,
        many bank-statement exports: a $50 purchase shows as `-50.00`, a
        $30 refund or payment as `30.00`.

    The route normalizes amounts to the `charges_positive` posture before
    running refund-skip and dedup logic, so downstream code stays
    issuer-agnostic. The default matches the more common monthly-
    statement convention; Gemini overrides it when the sample rows show
    a different pattern.
    """

    model_config = ConfigDict(extra="forbid")

    date: str
    merchant: str
    amount: str
    currency: str | None = None
    sign_convention: SignConvention = "charges_positive"
    confidence: float


class ColumnPreview(BaseModel):
    """`POST /imports/csv/preview` response, high-confidence branch.

    Returned when Gemini's column-detect confidence is >= 0.8. The client
    surfaces the mapping for user-visible confirmation and then re-uploads
    the file to `/commit` along with `import_token` and `column_mapping`.
    """

    model_config = ConfigDict(extra="forbid")

    detected_columns: ColumnMapping
    sample_rows: list[dict[str, str]]
    confidence: float
    import_token: str
    total_rows: int


class ManualMappingPreview(BaseModel):
    """`POST /imports/csv/preview` response, manual-mapping branch.

    Returned when Gemini's confidence is < 0.8. The client renders a
    column picker so the user can map `date`/`merchant`/`amount` (and
    optionally `currency`) manually; the picked `column_mapping` then
    rides through to `/commit`.
    """

    model_config = ConfigDict(extra="forbid")

    needs_manual_mapping: bool = True
    headers: list[str]
    sample_rows: list[dict[str, str]]
    import_token: str
    total_rows: int


class CsvCommitProgress(BaseModel):
    """One `event: progress` SSE frame from `/imports/csv/commit`.

    Emitted per row (not per batch) so the UI can show a smooth row
    counter. The bytes per frame are small enough that 5,000-row imports
    fit comfortably under the SSE concurrency budget (§17.5 line 1522).
    """

    model_config = ConfigDict(extra="forbid")

    processed: int
    total: int
    current_category: str


class CsvCommitDone(BaseModel):
    """Final `event: done` SSE frame from `/imports/csv/commit`.

    Counters mirror the skip-bucket decisions from DESIGN.md §5.4.3:
    duplicates skipped via the dedup quadruple, refunds skipped (negative
    amounts), rows skipped for currency mismatch, and rows we couldn't
    parse (malformed amount or unrecognized date format). `inserted` is
    the row count actually committed under `source = 'csv_import'`.
    """

    model_config = ConfigDict(extra="forbid")

    done: bool = True
    inserted: int
    skipped_duplicates: int
    skipped_refunds: int
    skipped_foreign_currency: int
    skipped_parse_errors: int
