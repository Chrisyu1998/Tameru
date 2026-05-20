"""CSV bank-import endpoints — Day 20.

Two endpoints make up the import flow:

  1. `POST /imports/csv/preview` — multipart upload + `card_id`.
     Verifies size and row-count caps, parses the header + first 5
     rows with stdlib `csv`, calls Gemini's `detect_columns`, and
     returns either `ColumnPreview` (high-confidence) or
     `ManualMappingPreview` (confidence < 0.8). An `import_token` —
     stateless HMAC of `(user_id, file_hash, detected_columns,
     expires_at)` — rides through to `/commit` so we don't have to
     persist anything between calls.

  2. `POST /imports/csv/commit` — multipart upload + form
     `{import_token, column_mapping, card_id}`. Verifies the token,
     re-reads the uploaded file, batch-categorizes in groups of 100
     via Gemini, dedups against `active_transactions` on
     `(user_id, date, normalize_merchant(merchant), amount)`, skips
     negative-amount rows and foreign-currency rows, and streams
     per-row SSE progress.

DESIGN.md §5.4.3 sets the limits (5 MB / 5,000 rows), the skip rules,
the dedup-quadruple resume key, and the no-recurring-detection rule.
CLAUDE.md invariant 8 names CSV as the v1 exception to the chat-only
write surface; invariant 14 keeps every `ai_call_log` row attributed to
the caller's JWT.
"""

from __future__ import annotations

import base64
import csv as _csv
import hashlib
import hmac
import io
import json
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Iterator
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.integrations.gemini import (
    GeminiError,
    categorize_batch,
    detect_columns,
)
from app.models.imports import (
    ColumnMapping,
    ColumnPreview,
    CsvCommitDone,
    CsvCommitProgress,
    ManualMappingPreview,
)
from app.util.merchant import normalize_merchant

router = APIRouter(prefix="/imports/csv", tags=["imports"])

# Hard caps — see DESIGN.md §5.4.3 ("Upload caps (v1)") and the §17.5
# line on rejecting oversized CSV uploads before parsing. 5 MB covers a
# ~2.5-year US bank export with headroom; 5,000 rows keeps the SSE
# stream wallclock tolerable (~50 batches × ~3s/batch ≈ 2-3 minutes).
_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_ROWS = 5_000

# Number of sample rows shown to Gemini at preview time; matches §5.4.3
# ("previews the first 5 rows for confirmation").
_PREVIEW_SAMPLE_ROWS = 5

# Token TTL — 15 minutes is plenty for a user to confirm column mapping
# and re-upload. Long enough to absorb a confused user; short enough
# that a stolen token is useless by the time it's exfiltrated.
_TOKEN_TTL_SECONDS = 15 * 60

# Match `gemini.categorize_batch._BATCH_MAX_SIZE` — keep them in sync.
# Duplicating the constant here keeps the route file self-contained for
# reading, but the integration module enforces the hard limit.
_BATCH_SIZE = 100

# Confidence threshold below which preview routes to the manual-mapping
# branch. Matches DESIGN.md §5.4.3 ("manual mapping UI").
_MIN_DETECT_CONFIDENCE = 0.8


@router.post("/preview")
def preview_csv(
    file: UploadFile = File(...),
    card_id: UUID = Form(...),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> ColumnPreview | ManualMappingPreview:
    """Inspect the upload and return a column mapping for confirmation.

    Returns `ColumnPreview` when Gemini's confidence >= 0.8, or
    `ManualMappingPreview` otherwise. The `import_token` in both
    branches is a stateless HMAC — see `_mint_import_token` — that
    `/commit` verifies, so no server-side storage is needed between
    calls. The card_id is held in the token payload too, which means
    the user can't sneak a different card_id past at commit time.

    422 surfaces for: empty CSV, malformed UTF-8, no header row.
    413 surfaces for: file > 5 MB, row count > 5,000.
    """
    _assert_card_owned(user, card_id)
    data = _read_upload_bytes(file)
    rows = _parse_csv_bytes(data)
    if not rows:
        raise _domain_error(
            "empty_csv",
            "CSV has no header row or no data rows",
        )

    headers = list(rows[0].keys())
    data_rows = rows[1:]
    total_rows = len(data_rows)
    if total_rows == 0:
        raise _domain_error(
            "empty_csv",
            "CSV has a header row but no data rows",
        )
    if total_rows > _MAX_ROWS:
        raise _too_large(
            f"CSV has {total_rows} data rows; max is {_MAX_ROWS}",
        )

    sample = data_rows[:_PREVIEW_SAMPLE_ROWS]
    try:
        mapping = detect_columns(headers, sample, user)
    except GeminiError as exc:
        raise _provider_error(exc) from exc

    file_hash = hashlib.sha256(data).hexdigest()
    token = _mint_import_token(
        user_id=user.user_id,
        card_id=card_id,
        file_hash=file_hash,
        detected_columns=mapping.model_dump(),
    )

    if mapping.confidence < _MIN_DETECT_CONFIDENCE:
        return ManualMappingPreview(
            headers=headers,
            sample_rows=sample,
            import_token=token,
            total_rows=total_rows,
        )
    return ColumnPreview(
        detected_columns=mapping,
        sample_rows=sample,
        confidence=mapping.confidence,
        import_token=token,
        total_rows=total_rows,
    )


@router.post("/commit")
def commit_csv(
    file: UploadFile = File(...),
    import_token: str = Form(...),
    column_mapping: str = Form(...),
    card_id: UUID = Form(...),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> StreamingResponse:
    """Insert the CSV's rows into `transactions` and stream progress.

    The wire shape on success is an SSE stream of `event: progress`
    frames (one per row) terminated by an `event: done` frame carrying
    the four counters. On Gemini failure mid-stream, an `event: error`
    frame surfaces and the route stops; already-committed rows stay
    committed and a re-upload is the recovery path (the dedup quadruple
    makes it idempotent — DESIGN.md §5.4.3).

    Per-row contract:
      * `amount < 0` → skip, increment `skipped_refunds`
      * `currency` column present and value != `users_meta.home_currency`
        → skip, increment `skipped_foreign_currency`
      * `(user_id, date, normalize_merchant(merchant), amount)` already
        present in `active_transactions` → skip,
        increment `skipped_duplicates`
      * otherwise insert with `source = 'csv_import'`,
        `client_request_id = NULL` (§8.2 line 834).
    """
    _assert_card_owned(user, card_id)
    try:
        mapping_payload = json.loads(column_mapping)
    except json.JSONDecodeError as exc:
        raise _domain_error(
            "invalid_column_mapping",
            "column_mapping must be a JSON object",
        ) from exc
    try:
        mapping = ColumnMapping(**mapping_payload)
    except Exception as exc:
        raise _domain_error(
            "invalid_column_mapping",
            f"column_mapping does not match ColumnMapping schema: {exc}",
        ) from exc

    data = _read_upload_bytes(file)
    rows = _parse_csv_bytes(data)
    if not rows:
        raise _domain_error("empty_csv", "CSV has no header row")
    data_rows = rows[1:]
    if not data_rows:
        raise _domain_error("empty_csv", "CSV has no data rows")
    if len(data_rows) > _MAX_ROWS:
        raise _too_large(f"CSV has {len(data_rows)} rows; max is {_MAX_ROWS}")

    file_hash = hashlib.sha256(data).hexdigest()
    _verify_import_token(
        token=import_token,
        user_id=user.user_id,
        card_id=card_id,
        file_hash=file_hash,
    )

    home_currency = _load_home_currency(user)
    candidates = _extract_candidates(data_rows, mapping, home_currency)
    dedup_set = _load_existing_dedup_set(user, candidates)

    def generate() -> Iterator[bytes]:
        """Run the commit pipeline and yield SSE frames as it goes."""
        client = supabase_for_user(user.jwt)
        past_corrections = _read_past_corrections(user)

        inserted = 0
        skipped_duplicates = 0
        skipped_refunds = 0
        skipped_foreign_currency = 0
        skipped_parse_errors = 0
        processed = 0
        total = len(data_rows)

        # Per-batch loop. Three phases per window:
        #   1. classify every row (parse_error / refund / foreign /
        #      dedup / insertable) — pure Python, no I/O.
        #   2. fire ONE categorize_batch + ONE bulk insert for the
        #      insertable subset. Bulk insert trades per-row error
        #      granularity for ~10x wallclock — see day-20 review.
        #   3. emit SSE progress frames in original CSV order so the
        #      bar advances smoothly, with the per-row counters
        #      tallied at frame-emit time.
        # Single-active-device (invariant 5) makes a same-user race
        # impossible, so we don't need the per-row `23505` recovery
        # path the row-at-a-time version carried.
        i = 0
        while i < total:
            window_candidates = candidates[i : i + _BATCH_SIZE]

            # Phase 1 — classify each row in window order. We also
            # pre-dedup WITHIN the batch: a CSV that repeats the same
            # row twice will only insert once (the partial unique
            # index would 23505 the second anyway), so we classify
            # the second occurrence as "dedup" up front rather than
            # sending it through Gemini + the RPC and miscounting it
            # as "inserted" in phase 3. `seen_in_batch` tracks keys
            # we've already marked for insertion in THIS window.
            seen_in_batch: set[tuple[str, str, str]] = set()
            outcomes: list[tuple[str, _RowCandidate | None]] = []
            batch_for_gemini: list[tuple[str, float]] = []
            batch_indices: list[int] = []
            for j, cand in enumerate(window_candidates):
                if cand is None:
                    outcomes.append(("parse_error", None))
                    continue
                if cand.is_refund:
                    outcomes.append(("refund", cand))
                    continue
                if cand.is_foreign:
                    outcomes.append(("foreign", cand))
                    continue
                key = (
                    cand.date_iso,
                    cand.merchant_normalized,
                    cand.amount_quantized,
                )
                if key in dedup_set or key in seen_in_batch:
                    outcomes.append(("dedup", cand))
                    continue
                seen_in_batch.add(key)
                outcomes.append(("insert", cand))
                batch_for_gemini.append((cand.merchant, float(cand.amount)))
                batch_indices.append(j)

            # Phase 2a — Gemini call for the insertable subset.
            categorizations = []
            if batch_for_gemini:
                try:
                    categorizations = categorize_batch(
                        batch_for_gemini, past_corrections, user
                    )
                except GeminiError as exc:
                    yield _sse_frame(
                        "error",
                        json.dumps({
                            "code": exc.error_code,
                            "message": str(exc),
                        }),
                    )
                    return
                except Exception as exc:
                    yield _sse_frame(
                        "error",
                        json.dumps({
                            "code": "unknown",
                            "message": str(exc),
                        }),
                    )
                    return

            # Defensive against a partial Gemini response — the
            # `_parse_batch_categorizations` length check should make
            # this unreachable, but the row-emit loop below assumes
            # one suggestion per insertable index.
            cat_by_idx = dict(zip(batch_indices, categorizations))
            if len(cat_by_idx) != len(batch_indices):
                yield _sse_frame(
                    "error",
                    json.dumps({
                        "code": "schema_violation",
                        "message": "categorizations missing for one or more rows",
                    }),
                )
                return

            # Phase 2b — bulk insert.
            insert_rows: list[dict[str, str]] = []
            for j in batch_indices:
                cand = window_candidates[j]
                # `cand` is non-None here by construction of
                # batch_indices (we only added "insert"-classified
                # rows above), but assert-narrow for the type
                # checker.
                assert cand is not None
                suggestion = cat_by_idx[j]
                insert_rows.append(
                    {
                        "user_id": str(user.user_id),
                        "card_id": str(card_id),
                        "merchant": cand.merchant_normalized,
                        "amount": str(cand.amount),
                        "date": cand.date_iso,
                        "category": suggestion.category,
                        "source": "csv_import",
                        "gemini_suggestion": suggestion.category,
                        # client_request_id stays NULL for CSV
                        # inserts (§8.2 line 834). Idempotency
                        # comes from the dedup quadruple, not
                        # crid.
                    }
                )

            race_lost_keys: set[tuple[str, str, str]] = set()
            if insert_rows:
                try:
                    # Call the SECURITY INVOKER plpgsql function
                    # `csv_import_bulk_insert` (migration
                    # 20260519160100) which emits the partial-
                    # index WHERE predicate so Postgres can use
                    # `transactions_csv_import_dedup_uniq` for
                    # ON CONFLICT inference. PostgREST's own
                    # upsert can't pass the partial predicate
                    # (42P10), so we encapsulate the INSERT in a
                    # function. Function returns the rows that
                    # actually landed; race-lost rows are absent
                    # from the result so we can reconcile the
                    # skipped_duplicates counter accurately.
                    # The function hardcodes user_id := auth.uid()
                    # so insert_rows drops `user_id` to make that
                    # intent explicit (tampering would be ignored
                    # anyway).
                    result = client.rpc(
                        "csv_import_bulk_insert",
                        {
                            "p_rows": [
                                {k: v for k, v in row.items() if k != "user_id"}
                                for row in insert_rows
                            ],
                        },
                    ).execute()
                except Exception as exc:
                    yield _sse_frame(
                        "error",
                        json.dumps({
                            "code": "insert_failed",
                            "message": str(exc),
                        }),
                    )
                    return

                # Identify which rows actually landed. PostgREST's
                # representation-return for an ignore-duplicates
                # upsert contains only the inserted rows; everything
                # missing was a race-lost duplicate. We dedupe by
                # the same quadruple the index uses.
                landed_keys: set[tuple[str, str, str]] = set()
                for row in result.data or []:
                    try:
                        landed_keys.add(
                            (
                                str(row["date"]),
                                normalize_merchant(str(row["merchant"])),
                                _quantize_amount(Decimal(str(row["amount"]))),
                            )
                        )
                    except (KeyError, InvalidOperation):
                        continue

                # Roll dedup_set forward for every row that landed,
                # AND record the race-lost set so the SSE emit loop
                # can attribute them to `skipped_duplicates`
                # correctly instead of overcounting `inserted`.
                for j in batch_indices:
                    cand = window_candidates[j]
                    assert cand is not None
                    key = (
                        cand.date_iso,
                        cand.merchant_normalized,
                        cand.amount_quantized,
                    )
                    if key in landed_keys:
                        dedup_set.add(key)
                    else:
                        race_lost_keys.add(key)
                        dedup_set.add(key)

            # Phase 3 — emit SSE in original CSV order with the
            # per-row counters tallied. Progress arrives in a tight
            # burst after the bulk insert; the visual stall is one
            # batch's worth of latency (~50ms), imperceptible at
            # 60fps.
            for j, (kind, _cand) in enumerate(outcomes):
                processed += 1
                if kind == "parse_error":
                    skipped_parse_errors += 1
                    yield _sse_frame(
                        "progress",
                        CsvCommitProgress(
                            processed=processed,
                            total=total,
                            current_category="",
                        ).model_dump_json(),
                    )
                    continue
                if kind == "refund":
                    skipped_refunds += 1
                    yield _sse_frame(
                        "progress",
                        CsvCommitProgress(
                            processed=processed,
                            total=total,
                            current_category="",
                        ).model_dump_json(),
                    )
                    continue
                if kind == "foreign":
                    skipped_foreign_currency += 1
                    yield _sse_frame(
                        "progress",
                        CsvCommitProgress(
                            processed=processed,
                            total=total,
                            current_category="",
                        ).model_dump_json(),
                    )
                    continue
                if kind == "dedup":
                    skipped_duplicates += 1
                    yield _sse_frame(
                        "progress",
                        CsvCommitProgress(
                            processed=processed,
                            total=total,
                            current_category="",
                        ).model_dump_json(),
                    )
                    continue
                # kind == "insert" — but the row may have lost the
                # race to a concurrent /commit. Race-lost rows are
                # attributed to skipped_duplicates so the final
                # counters reconcile with what the DB actually
                # holds. The category readout for race-losers blanks
                # out since no row landed under our Gemini call.
                assert _cand is not None
                key = (
                    _cand.date_iso,
                    _cand.merchant_normalized,
                    _cand.amount_quantized,
                )
                if key in race_lost_keys:
                    skipped_duplicates += 1
                    yield _sse_frame(
                        "progress",
                        CsvCommitProgress(
                            processed=processed,
                            total=total,
                            current_category="",
                        ).model_dump_json(),
                    )
                    continue
                suggestion = cat_by_idx[j]
                inserted += 1
                yield _sse_frame(
                    "progress",
                    CsvCommitProgress(
                        processed=processed,
                        total=total,
                        current_category=suggestion.category,
                    ).model_dump_json(),
                )

            i += _BATCH_SIZE

        done = CsvCommitDone(
            inserted=inserted,
            skipped_duplicates=skipped_duplicates,
            skipped_refunds=skipped_refunds,
            skipped_foreign_currency=skipped_foreign_currency,
            skipped_parse_errors=skipped_parse_errors,
        )
        yield _sse_frame("done", done.model_dump_json())

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

class _RowCandidate:
    """Parsed-and-classified view of one CSV row.

    `is_refund` and `is_foreign` are precomputed so the SSE loop can
    skip without re-parsing. `amount_quantized` matches the format the
    dedup-set lookup uses (two decimal places as a string) so set
    membership is deterministic regardless of how the source CSV wrote
    "5", "5.0", or "5.00".
    """

    __slots__ = (
        "merchant",
        "merchant_normalized",
        "amount",
        "amount_quantized",
        "date_iso",
        "is_refund",
        "is_foreign",
    )

    def __init__(
        self,
        *,
        merchant: str,
        merchant_normalized: str,
        amount: Decimal,
        amount_quantized: str,
        date_iso: str,
        is_refund: bool,
        is_foreign: bool,
    ) -> None:
        """Initialize a row candidate."""
        self.merchant = merchant
        self.merchant_normalized = merchant_normalized
        self.amount = amount
        self.amount_quantized = amount_quantized
        self.date_iso = date_iso
        self.is_refund = is_refund
        self.is_foreign = is_foreign


def _assert_card_owned(user: AuthedUser, card_id: UUID) -> None:
    """Mirror Day 5's confirm-path ownership check.

    RLS on `cards` returns empty for another user's card id; the
    `status = 'active'` filter additionally excludes soft-deleted cards.
    All three failure modes (non-existent, cross-tenant, deleted)
    collapse to 422 so a probing client can't enumerate ids.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("cards")
        .select("id")
        .eq("id", str(card_id))
        .eq("status", "active")
        .execute()
    )
    if not resp.data:
        raise _domain_error(
            "invalid_card",
            "card_id does not resolve to one of your cards",
        )


def _read_upload_bytes(file: UploadFile) -> bytes:
    """Read the upload, rejecting > 5 MB before parsing.

    Two-stage check: prefer `UploadFile.size` (cheap, set by starlette
    from Content-Length) and fall back to a post-read length check so
    a missing or lying header still fails closed. Either way we never
    parse a too-big payload.
    """
    declared = getattr(file, "size", None)
    if declared is not None and declared > _MAX_FILE_BYTES:
        raise _too_large(
            f"upload is {declared} bytes; max is {_MAX_FILE_BYTES}",
        )
    data = file.file.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise _too_large(
            f"upload exceeds {_MAX_FILE_BYTES} bytes",
        )
    return data


def _parse_csv_bytes(data: bytes) -> list[dict[str, str]]:
    """Decode + parse the CSV. Returns [{header: value, ...}, ...].

    Index 0 of the returned list is the header row (each value being
    its own column name). This lets callers grab headers without
    consuming the iterator twice. Empty cells stay as empty strings;
    DictReader handles ragged rows by mapping extra cells to None
    which we coerce to "" so the wire shape is always `dict[str, str]`.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _domain_error(
            "invalid_encoding",
            "CSV must be UTF-8 encoded",
        ) from exc
    reader = _csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    if not headers:
        return []
    out: list[dict[str, str]] = [
        {h: h for h in headers}  # synthetic header row at index 0
    ]
    for row in reader:
        out.append({h: (row.get(h) or "") for h in headers})
    return out


def _extract_candidates(
    data_rows: list[dict[str, str]],
    mapping: ColumnMapping,
    home_currency: str,
) -> list[_RowCandidate | None]:
    """Project each row through `mapping`, classify, and return.

    Two normalization steps run before downstream classification:

    1. **Amount parsing** via `_parse_amount` handles currency-formatted
       strings (`$1,234.56`, accounting `(45.00)` negatives, etc.).
    2. **Sign flip** when `mapping.sign_convention == 'charges_negative'`
       turns issuer-inverted exports (Chase activity, Citi activity)
       into the canonical "positive = charge, negative = refund" posture.
       Without this, every purchase from a `charges_negative` CSV would
       fall into the refund-skip bucket and the import would land with
       almost nothing imported — Codex's P1 finding on the Day 20 patch.

    A row that fails to parse (missing amount, malformed number,
    unrecognized date) maps to `None` so the SSE loop attributes it to
    the `skipped_parse_errors` bucket; the count surfaces in the done
    frame so the user sees a real signal when their CSV has rows we
    couldn't read.
    """
    flip_sign = mapping.sign_convention == "charges_negative"
    out: list[_RowCandidate | None] = []
    for row in data_rows:
        merchant_raw = (row.get(mapping.merchant) or "").strip()
        amount_raw = (row.get(mapping.amount) or "").strip()
        date_raw = (row.get(mapping.date) or "").strip()
        if not merchant_raw or not amount_raw or not date_raw:
            out.append(None)
            continue
        amount = _parse_amount(amount_raw)
        if amount is None:
            out.append(None)
            continue
        if flip_sign:
            amount = -amount
        try:
            date_iso = _normalize_date(date_raw)
        except ValueError:
            out.append(None)
            continue
        is_refund = amount < 0
        is_foreign = False
        if mapping.currency:
            currency_raw = (row.get(mapping.currency) or "").strip().upper()
            if currency_raw and currency_raw != home_currency.upper():
                is_foreign = True
        out.append(
            _RowCandidate(
                merchant=merchant_raw,
                merchant_normalized=normalize_merchant(merchant_raw),
                amount=amount,
                amount_quantized=_quantize_amount(amount),
                date_iso=date_iso,
                is_refund=is_refund,
                is_foreign=is_foreign,
            )
        )
    return out


def _parse_amount(raw: str) -> Decimal | None:
    """Parse a bank-CSV amount cell with US formatting conventions.

    Accepts the shapes we've seen in real-world Chase / Amex / BofA /
    Capital One / Wells Fargo / Citi exports:

      * `5.50` / `5` / `1234.56`          — plain
      * `1,234.56`                         — thousands separator
      * `$5.50` / `$1,234.56`              — leading currency symbol
      * `-5.50` / `-$5.50` / `$-5.50`      — explicit negative (refund)
      * `(5.50)` / `($5.50)`               — accounting parens-negative
                                             (Amex, Wells statement
                                             exports use this)

    Returns None on inputs we can't recognize so the caller surfaces
    them as parse_errors rather than crashing. European decimal-comma
    (`5,50`) is deliberately NOT supported — invariant 13 anchors
    everything to the user's home currency and Tameru only supports
    USD/EUR/etc. as US-formatted exports for v1.
    """
    s = raw.strip()
    if not s:
        return None
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1].strip()
    s = s.replace(",", "").replace("$", "").strip()
    try:
        value = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -value if is_negative else value


def _normalize_date(value: str) -> str:
    """Parse common bank CSV date formats; return ISO `YYYY-MM-DD`.

    Tries the formats US bank exports actually use, in priority order.
    Raises ValueError on no match — the caller falls into the silent
    parse-error bucket. Per CLAUDE.md invariant 13 we don't infer
    timezone here; dates are calendar dates on the user's statement,
    not timestamps.
    """
    import datetime as _dt

    formats = (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    )
    for fmt in formats:
        try:
            return _dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"date {value!r} did not match any known format")


def _quantize_amount(value: Decimal) -> str:
    """Render a Decimal to a stable two-place string for dedup.

    `transactions.amount` is `numeric` and Supabase returns it as a
    string; quantizing both sides of the dedup comparison to
    `0.01` keeps "5", "5.0", "5.00" all looking like the same row.
    """
    return str(value.quantize(Decimal("0.01")))


def _load_home_currency(user: AuthedUser) -> str:
    """Read the user's home currency for the foreign-row skip rule.

    `home_currency` is immutable at signup (invariant 13), so this
    read is cheap and stable for the lifetime of the request. RLS
    scopes the read to the caller's row.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("users_meta")
        .select("home_currency")
        .eq("user_id", str(user.user_id))
        .execute()
    )
    if not resp.data:
        raise _domain_error(
            "no_users_meta",
            "users_meta row missing — finish onboarding before importing",
        )
    return resp.data[0]["home_currency"]


def _load_existing_dedup_set(
    user: AuthedUser, candidates: list[_RowCandidate | None]
) -> set[tuple[str, str, str]]:
    """Bulk-fetch existing transactions in the candidate date range.

    Trades a single ranged SELECT for N point-lookups — at 5,000 rows
    the point-lookup path would run ~5,000 round-trips and the SSE
    stream would block on DB latency. The set values are tuples of
    `(date_iso, merchant_normalized, amount_quantized)` so set
    membership check is a string-tuple hash — fast and deterministic.

    Reads through `active_transactions` so a soft-deleted prior import
    does not shadow a re-import (DESIGN.md §5.4.3, the dedup-query
    "active vs base" decision in the Day 20 prompt).
    """
    parsed = [c for c in candidates if c is not None and not c.is_refund and not c.is_foreign]
    if not parsed:
        return set()
    dates = sorted({c.date_iso for c in parsed})
    date_min, date_max = dates[0], dates[-1]
    client = supabase_for_user(user.jwt)
    out: set[tuple[str, str, str]] = set()
    page_size = 1000
    offset = 0
    while True:
        resp = (
            client.table("active_transactions")
            .select("date, merchant, amount")
            .gte("date", date_min)
            .lte("date", date_max)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        for row in rows:
            try:
                amount = Decimal(str(row["amount"]))
            except (InvalidOperation, KeyError, TypeError):
                continue
            out.add(
                (
                    str(row["date"]),
                    normalize_merchant(str(row["merchant"])),
                    _quantize_amount(amount),
                )
            )
        if len(rows) < page_size:
            break
        offset += page_size
    return out


def _read_past_corrections(user: AuthedUser) -> list[tuple[str, str]]:
    """Reuse `gemini._read_past_corrections` for the batch prompt.

    Reimported here so the route file documents the dependency at the
    boundary — categorize_batch consumes the same shape Day 4's
    per-row `categorize` does. Keeping it as a thin wrapper means a
    future divergence between the two paths shows up here, not in a
    silent caller-side reach into the gemini module's private helpers.
    """
    from app.integrations.gemini import _read_past_corrections as _impl  # noqa: PLC0415 — local import keeps the route's import block thin

    return _impl(user)


def _mint_import_token(
    *,
    user_id: UUID,
    card_id: UUID,
    file_hash: str,
    detected_columns: dict,
) -> str:
    """Sign `(user_id, card_id, file_hash, detected_columns, expires_at)`.

    Stateless intent token — no server-side storage. The payload is
    JSON-encoded, base64-url-encoded, then HMAC-signed with
    `IMPORT_TOKEN_SECRET`. Verify by recomputing the signature with
    `hmac.compare_digest` and checking the embedded claims.
    """
    payload = {
        "user_id": str(user_id),
        "card_id": str(card_id),
        "file_hash": file_hash,
        "detected_columns": detected_columns,
        "expires_at": int(time.time()) + _TOKEN_TTL_SECONDS,
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")
    sig = hmac.new(
        _import_token_secret().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_import_token(
    *,
    token: str,
    user_id: UUID,
    card_id: UUID,
    file_hash: str,
) -> None:
    """Recompute + compare signature, claims, and expiry.

    422 on any mismatch — a tampered token is indistinguishable from
    an expired one from the user's seat (both mean "your preview is
    stale; re-do it"). `hmac.compare_digest` defeats timing attacks.
    """
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError as exc:
        raise _domain_error(
            "invalid_import_token",
            "import_token is malformed",
        ) from exc
    expected_sig = hmac.new(
        _import_token_secret().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise _domain_error(
            "invalid_import_token",
            "import_token signature does not verify",
        )
    try:
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(payload_json)
    except Exception as exc:
        raise _domain_error(
            "invalid_import_token",
            "import_token payload is malformed",
        ) from exc
    if payload.get("user_id") != str(user_id):
        raise _domain_error(
            "invalid_import_token",
            "import_token does not match the authenticated user",
        )
    if payload.get("card_id") != str(card_id):
        raise _domain_error(
            "invalid_import_token",
            "import_token does not match the supplied card_id",
        )
    if payload.get("file_hash") != file_hash:
        raise _domain_error(
            "invalid_import_token",
            "uploaded file does not match the file from /preview",
        )
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, int) or expires_at < int(time.time()):
        raise _domain_error(
            "invalid_import_token",
            "import_token has expired — re-run /preview",
        )


def _import_token_secret() -> str:
    """Return the HMAC secret. Fail fast if unset.

    Same fail-fast posture as `_model_name()` — configuration without a
    default. Tests set this via `monkeypatch.setenv`; production sets
    it in the Railway environment. Rotating the secret invalidates all
    live `import_token`s, which is exactly the desired property if a
    secret leaks.
    """
    secret = os.environ.get("IMPORT_TOKEN_SECRET")
    if not secret:
        raise RuntimeError(
            "IMPORT_TOKEN_SECRET is not set. Required for /imports/csv/preview "
            "and /imports/csv/commit to share state across calls."
        )
    return secret


def _sse_frame(event: str, data: str) -> bytes:
    """Encode one SSE frame as bytes.

    Mirrors `app/routes/chat.py::_sse_frame`. Duplicated rather than
    factored into a shared util so the chat module's protocol stays
    self-contained — if the import path ever needs different framing
    (binary chunks for a future bulk export, etc.) the divergence is
    local.
    """
    lines = [f"event: {event}"]
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _domain_error(code: str, message: str) -> HTTPException:
    """422 with the project's standard `{code, message}` body."""
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _too_large(message: str) -> HTTPException:
    """413 with the standard body shape.

    Hard-code 413 like `transactions.py` hard-codes 422 — starlette
    renamed `HTTP_413_REQUEST_ENTITY_TOO_LARGE` to `HTTP_413_CONTENT_TOO_LARGE`
    mid-release; pinning the int sidesteps the deprecation churn.
    """
    return HTTPException(
        status_code=413,
        detail={"code": "payload_too_large", "message": message},
    )


def _provider_error(exc: GeminiError) -> HTTPException:
    """503 surface for an upstream Gemini failure during /preview.

    `/commit` surfaces the same condition as an SSE error frame
    instead, because the response is already streaming when Gemini
    fails. /preview is a normal JSON response, so we use HTTP
    semantics.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": exc.error_code, "message": str(exc)},
    )
