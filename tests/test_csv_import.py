"""Day 20 — CSV import route tests.

Gemini is mocked at the route's import sites — non-deterministic LLM
output must not gate CI. LLM-quality assertions (does Gemini actually
identify the date column on a real Chase header?) belong in
`evals/csv_import/`, not here.

The fixtures live in `tests/fixtures/csv/`. Each is hand-rolled to
exercise a specific code path:

  * `chase_sample.csv` — typical US CC export. Includes one refund row
    (negative amount) for the skipped_refunds bucket.
  * `amex_sample.csv` — different header naming convention; covers the
    happy path with a second issuer's shape.
  * `bofa_with_currency.csv` — has a per-row Currency column with
    USD/EUR/GBP values for the skipped_foreign_currency bucket.
  * `weird_headers.csv` — opaque header names that force Gemini into a
    low-confidence response; the test mocks the response.

Every test that talks to Supabase wipes the per-user transactions table
in setup so re-import dedup is unambiguous.
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import supabase_for_user
from app.integrations.gemini import CategorySuggestion
from app.main import app
from app.models.imports import ColumnMapping
from app.routes import imports as imports_module

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "csv"


# ---------------------------------------------------------------------------
# Shared fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def import_token_secret(monkeypatch) -> None:
    """Set IMPORT_TOKEN_SECRET so the route's helper doesn't fail-fast."""
    monkeypatch.setenv("IMPORT_TOKEN_SECRET", "test-only-not-real")


@pytest.fixture
def http_client() -> TestClient:
    """Provide a TestClient bound to the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def clean_transactions(user_a):
    """Wipe user_a's transactions so dedup-related assertions are clean.

    Day 20's dedup quadruple reads from `active_transactions`; we wipe
    the base table here (DELETE applies under the user's JWT so RLS
    scopes the wipe). Fires before each test that opts in via the
    `clean_transactions` parameter.
    """
    client = supabase_for_user(user_a.jwt)
    client.table("transactions").delete().eq("user_id", user_a.id).execute()
    yield


@pytest.fixture
def clean_ai_call_log(user_a, admin_client):
    """Drop user_a's ai_call_log rows from today so counts are unambiguous."""
    today = _dt.datetime.now(_dt.timezone.utc).date()
    midnight = _dt.datetime.combine(today, _dt.time.min, tzinfo=_dt.timezone.utc)
    admin_client.table("ai_call_log").delete().eq("user_id", user_a.id).gte(
        "timestamp", midnight.isoformat()
    ).execute()
    yield


# ---------------------------------------------------------------------------
# Preview — column detection happy paths.
# ---------------------------------------------------------------------------


def test_preview_chase_returns_column_mapping(
    http_client, user_a, card_a, clean_ai_call_log, monkeypatch
):
    """Chase CSV preview returns a high-confidence ColumnMapping."""
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    _install_detect_mock(monkeypatch, detected)

    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("chase.csv", _read_fixture("chase_sample.csv"), "text/csv")},
        data={"card_id": card_a},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detected_columns"]["date"] == "Transaction Date"
    assert body["detected_columns"]["merchant"] == "Description"
    assert body["detected_columns"]["amount"] == "Amount"
    assert body["detected_columns"]["currency"] is None
    assert body["confidence"] == 0.95
    assert body["total_rows"] == 10
    assert len(body["sample_rows"]) == 5
    assert "import_token" in body and "." in body["import_token"]


def test_preview_amex_returns_column_mapping(
    http_client, user_a, card_a, clean_ai_call_log, monkeypatch
):
    """Amex CSV preview returns a high-confidence ColumnMapping."""
    detected = ColumnMapping(
        date="Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.92,
    )
    _install_detect_mock(monkeypatch, detected)

    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("amex.csv", _read_fixture("amex_sample.csv"), "text/csv")},
        data={"card_id": card_a},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detected_columns"]["date"] == "Date"
    assert body["total_rows"] == 10


def test_preview_bofa_with_currency_column(
    http_client, user_a, card_a, clean_ai_call_log, monkeypatch
):
    """BofA fixture has a Currency column — surfaces as ColumnMapping.currency."""
    detected = ColumnMapping(
        date="Posted Date",
        merchant="Payee",
        amount="Amount",
        currency="Currency",
        confidence=0.91,
    )
    _install_detect_mock(monkeypatch, detected)

    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={
            "file": ("bofa.csv", _read_fixture("bofa_with_currency.csv"), "text/csv"),
        },
        data={"card_id": card_a},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detected_columns"]["currency"] == "Currency"


# ---------------------------------------------------------------------------
# Preview — manual-mapping branch and ai_call_log accounting.
# ---------------------------------------------------------------------------


def test_preview_low_confidence_returns_manual_mapping(
    http_client, user_a, card_a, clean_ai_call_log, monkeypatch
):
    """Confidence < 0.8 routes to the ManualMappingPreview branch."""
    detected = ColumnMapping(
        date="field_a",
        merchant="field_b",
        amount="field_c",
        currency=None,
        confidence=0.4,
    )
    _install_detect_mock(monkeypatch, detected)

    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("weird.csv", _read_fixture("weird_headers.csv"), "text/csv")},
        data={"card_id": card_a},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["needs_manual_mapping"] is True
    assert body["headers"] == ["field_a", "field_b", "field_c", "field_d"]
    assert body["total_rows"] == 3
    assert "import_token" in body


def test_preview_logs_one_ai_call_log_row(
    http_client, user_a, card_a, admin_client, clean_ai_call_log, monkeypatch
):
    """A successful /preview writes exactly one task_type='csv_import' row."""
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    _install_detect_mock(monkeypatch, detected)

    http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("chase.csv", _read_fixture("chase_sample.csv"), "text/csv")},
        data={"card_id": card_a},
    )

    rows = _ai_call_log_today(user_a, admin_client)
    csv_rows = [r for r in rows if r["task_type"] == "csv_import"]
    assert len(csv_rows) == 1
    assert csv_rows[0]["prompt_version"] == "csv_detect_v1"


# ---------------------------------------------------------------------------
# Preview — limits and validation.
# ---------------------------------------------------------------------------


def test_preview_rejects_large_file_with_413(http_client, user_a, card_a):
    """Upload > 5 MB rejected with 413 before parsing."""
    # 6 MB of padded text — first row is a header so the validator
    # doesn't 422 on shape before it 413s on size. The body never
    # reaches `detect_columns` because the size check runs first.
    body = b"date,merchant,amount\n" + (b"2026-04-01,test,1.00\n" * (6 * 1024 * 1024 // 24))
    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("huge.csv", body, "text/csv")},
        data={"card_id": card_a},
    )
    assert resp.status_code == 413
    payload = resp.json()
    assert payload["detail"]["code"] == "payload_too_large"


def test_preview_rejects_too_many_rows_with_413(http_client, user_a, card_a):
    """A CSV with > 5,000 data rows is rejected with 413."""
    # Build a 5,001-row CSV under the 5 MB byte cap.
    header = "date,merchant,amount\n"
    row = "2026-04-01,t,1.00\n"  # 20 bytes per row -> ~100 KB total
    body = (header + row * 5001).encode("utf-8")
    assert len(body) < 5 * 1024 * 1024
    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("too_many.csv", body, "text/csv")},
        data={"card_id": card_a},
    )
    assert resp.status_code == 413


def test_preview_rejects_unknown_card_with_422(http_client, user_a):
    """A card_id that doesn't resolve for the caller is rejected as 422."""
    import uuid

    resp = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("chase.csv", _read_fixture("chase_sample.csv"), "text/csv")},
        data={"card_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_card"


# ---------------------------------------------------------------------------
# Commit — happy path.
# ---------------------------------------------------------------------------


def test_commit_within_batch_duplicate_counts_as_skipped_not_inserted(
    http_client,
    user_a,
    card_a,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """Two identical rows in the same CSV → 1 inserted, 1 skipped_duplicate.

    Pins the within-batch dedup classification: the partial unique
    index would 23505 the second row anyway, so we mark it as a
    duplicate up front (not "inserted") so the SSE counters match
    what's actually in the database.
    """
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    status, frames = _do_preview_then_commit(
        http_client,
        user_a,
        card_a,
        "chase_within_batch_dup.csv",
        detected,
        monkeypatch,
    )
    assert status == 200
    done = json.loads([f for f in frames if f[0] == "done"][0][1])
    # Fixture: 2 identical Blue Bottle rows + 1 Whole Foods. Expected:
    # 2 inserts (one BB, one WF), 1 within-batch duplicate.
    assert done["inserted"] == 2
    assert done["skipped_duplicates"] == 1
    assert done["skipped_refunds"] == 0
    assert done["skipped_foreign_currency"] == 0
    assert done["skipped_parse_errors"] == 0

    sb = supabase_for_user(user_a.jwt)
    rows = (
        sb.table("transactions")
        .select("merchant, amount")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    # Exactly 2 distinct rows in the DB (not 3) — the partial unique
    # index would have caught the second BB anyway; the in-Python
    # pre-dedup just keeps the counters honest.
    assert len(rows) == 2


def test_commit_handles_charges_negative_convention(
    http_client,
    user_a,
    card_a,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """A `charges_negative` CSV imports purchases as positive transactions.

    Codex P1: Chase activity / Citi activity exports flip the sign so
    that purchases are negative and refunds/payments are positive. Without
    handling this, every charge would fall into `skipped_refunds` and the
    import would land with almost nothing imported.

    Fixture: 4 purchases (negative), 1 refund (positive), 1 payment
    (positive). After the sign flip:
      * 4 purchases → positive → 4 inserts
      * 1 refund → negative → skipped_refunds
      * 1 payment → negative → skipped_refunds (Tameru treats payments
        as refund-like for v1 — they're not user-initiated spending)
    """
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        sign_convention="charges_negative",
        confidence=0.95,
    )
    status, frames = _do_preview_then_commit(
        http_client,
        user_a,
        card_a,
        "chase_activity_charges_negative.csv",
        detected,
        monkeypatch,
    )
    assert status == 200
    done = json.loads([f for f in frames if f[0] == "done"][0][1])
    assert done["inserted"] == 4
    assert done["skipped_refunds"] == 2  # refund + payment both flip to negative
    assert done["skipped_duplicates"] == 0
    assert done["skipped_foreign_currency"] == 0
    assert done["skipped_parse_errors"] == 0

    sb = supabase_for_user(user_a.jwt)
    rows = (
        sb.table("transactions")
        .select("amount, merchant")
        .eq("user_id", user_a.id)
        .order("amount", desc=True)
        .execute()
        .data
    )
    # All 4 charges land as POSITIVE amounts in the DB so the
    # breakdown and goals do the right math.
    assert len(rows) == 4
    for r in rows:
        assert Decimal(str(r["amount"])) > 0


def test_commit_handles_currency_formatted_amounts(
    http_client,
    user_a,
    card_a,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """`$1,234.56`, `($45.00)`, `-$22.50` all parse correctly.

    Bank CSVs commonly quote amounts with currency symbols and
    thousands separators (Chase / Amex / Wells Fargo statement
    exports do this once a row exceeds $999 or carries a return).
    `Decimal("$1,234.56")` would raise — `_parse_amount` strips
    `$` and `,` and handles accounting parens for negatives.
    """
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    status, frames = _do_preview_then_commit(
        http_client,
        user_a,
        card_a,
        "chase_formatted_amounts.csv",
        detected,
        monkeypatch,
    )
    assert status == 200
    done = json.loads([f for f in frames if f[0] == "done"][0][1])
    # Fixture rows:
    #   1. $1,234.56  → insert
    #   2. ($45.00)   → refund (accounting negative)
    #   3. 15.75      → insert
    #   4. -$22.50    → refund
    #   5. ??garbage  → parse error
    assert done["inserted"] == 2
    assert done["skipped_refunds"] == 2
    assert done["skipped_parse_errors"] == 1
    assert done["skipped_duplicates"] == 0
    assert done["skipped_foreign_currency"] == 0

    sb = supabase_for_user(user_a.jwt)
    rows = (
        sb.table("transactions")
        .select("amount")
        .eq("user_id", user_a.id)
        .order("amount", desc=True)
        .execute()
        .data
    )
    # Big-ticket parsed correctly (not e.g. 234.56 from a stripped
    # leading "1,").
    amounts = [str(r["amount"]) for r in rows]
    assert "1234.56" in amounts
    assert "15.75" in amounts


def test_commit_chase_inserts_rows_with_csv_import_source(
    http_client,
    user_a,
    card_a,
    admin_client,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """Commit happy path: rows land with source='csv_import', client_request_id NULL."""
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    status, frames = _do_preview_then_commit(
        http_client, user_a, card_a, "chase_sample.csv", detected, monkeypatch
    )
    assert status == 200
    done_frames = [f for f in frames if f[0] == "done"]
    error_frames = [f for f in frames if f[0] == "error"]
    assert not error_frames, error_frames
    assert len(done_frames) == 1, frames
    done = json.loads(done_frames[0][1])
    # 10 rows total; 1 negative amount (Amazon return) skipped as refund;
    # 9 inserted, 0 duplicates, 0 foreign, 0 parse errors.
    assert done["inserted"] == 9
    assert done["skipped_refunds"] == 1
    assert done["skipped_duplicates"] == 0
    assert done["skipped_foreign_currency"] == 0
    assert done["skipped_parse_errors"] == 0

    progress_frames = [f for f in frames if f[0] == "progress"]
    assert len(progress_frames) == 10  # one frame per row, including the skipped refund

    sb = supabase_for_user(user_a.jwt)
    rows = (
        sb.table("transactions")
        .select("*")
        .eq("user_id", user_a.id)
        .execute()
        .data
    )
    assert len(rows) == 9
    for r in rows:
        assert r["source"] == "csv_import"
        assert r["client_request_id"] is None
        assert r["card_id"] == card_a


def test_commit_skips_foreign_currency_rows(
    http_client,
    user_a,
    card_a,
    admin_client,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """Rows whose Currency != home_currency (USD) skip into the bucket."""
    detected = ColumnMapping(
        date="Posted Date",
        merchant="Payee",
        amount="Amount",
        currency="Currency",
        confidence=0.91,
    )
    status, frames = _do_preview_then_commit(
        http_client, user_a, card_a, "bofa_with_currency.csv", detected, monkeypatch
    )
    assert status == 200
    done = json.loads([f for f in frames if f[0] == "done"][0][1])
    # 10 rows; 3 non-USD (EUR x2, GBP x1) skipped; 7 inserted.
    assert done["inserted"] == 7
    assert done["skipped_foreign_currency"] == 3
    assert done["skipped_refunds"] == 0
    assert done["skipped_duplicates"] == 0


def test_commit_dedups_on_repeat_import(
    http_client,
    user_a,
    card_a,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """Re-running the same import inserts 0 rows; all flagged duplicates."""
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    # First run — populate.
    _do_preview_then_commit(
        http_client, user_a, card_a, "chase_sample.csv", detected, monkeypatch
    )
    # Second run — every non-refund row should hit dedup.
    status, frames = _do_preview_then_commit(
        http_client, user_a, card_a, "chase_sample.csv", detected, monkeypatch
    )
    assert status == 200
    done = json.loads([f for f in frames if f[0] == "done"][0][1])
    assert done["inserted"] == 0
    assert done["skipped_duplicates"] == 9
    assert done["skipped_refunds"] == 1  # refund still classified separately


def test_commit_uses_bulk_rpc_per_batch(
    http_client,
    user_a,
    card_a,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """One csv_import_bulk_insert RPC call per window.

    Wrap `client.rpc("csv_import_bulk_insert", ...)` to count calls and
    inspect the payload shape. With the Chase fixture (9 insertable
    rows in one window) we expect exactly one RPC call whose `p_rows`
    is a list of 9 dicts — none of which carry `user_id` (the function
    hardcodes that from auth.uid() so a tampered client can't
    mis-attribute).
    """
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )

    rpc_calls: list[tuple[str, dict]] = []
    from app.db import supabase_for_user as _real_supabase_for_user

    def wrapped_supabase(jwt):
        """Wrap the user client so we can spy on .rpc() calls."""
        client = _real_supabase_for_user(jwt)
        real_rpc = client.rpc

        def rpc_proxy(name, params=None):
            """Record the call name + params, then forward verbatim."""
            rpc_calls.append((name, dict(params or {})))
            return real_rpc(name, params)

        client.rpc = rpc_proxy  # type: ignore[assignment]
        return client

    # Patch only the route module's reference — gemini.py + integration
    # helpers still use the unwrapped client so the categorize_batch
    # mock's ai_call_log writes are unaffected.
    monkeypatch.setattr(imports_module, "supabase_for_user", wrapped_supabase)

    status, frames = _do_preview_then_commit(
        http_client, user_a, card_a, "chase_sample.csv", detected, monkeypatch
    )
    assert status == 200
    done = json.loads([f for f in frames if f[0] == "done"][0][1])
    assert done["inserted"] == 9

    # Exactly one csv_import_bulk_insert RPC for the single window of
    # 9 insertables (the refund is filtered out before the RPC).
    bulk_calls = [c for c in rpc_calls if c[0] == "csv_import_bulk_insert"]
    assert len(bulk_calls) == 1
    _name, params = bulk_calls[0]
    p_rows = params["p_rows"]
    assert len(p_rows) == 9
    for row in p_rows:
        assert row["source"] == "csv_import"
        # The SQL function hardcodes user_id := auth.uid(); the route
        # drops the key from the payload to make that explicit.
        assert "user_id" not in row
        assert "client_request_id" not in row  # NULL via column default


def test_commit_logs_ai_call_log_rows_per_batch(
    http_client,
    user_a,
    card_a,
    admin_client,
    clean_transactions,
    clean_ai_call_log,
    monkeypatch,
):
    """Preview writes 1 row; commit writes ceil(insertable_rows / 100) rows.

    Chase fixture has 10 data rows, 9 of which are insertable (the
    refund is filtered out before Gemini sees it) → 1 batch call.
    Total expected ai_call_log rows with task_type='csv_import': 2.
    """
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    _do_preview_then_commit(
        http_client, user_a, card_a, "chase_sample.csv", detected, monkeypatch
    )
    rows = _ai_call_log_today(user_a, admin_client)
    csv_rows = [r for r in rows if r["task_type"] == "csv_import"]
    assert len(csv_rows) == 2
    versions = sorted(r["prompt_version"] for r in csv_rows)
    assert versions == ["csv_batch_v1", "csv_detect_v1"]


# ---------------------------------------------------------------------------
# Commit — token verification.
# ---------------------------------------------------------------------------


def test_commit_rejects_tampered_token(
    http_client, user_a, card_a, clean_transactions, monkeypatch
):
    """Flipping the signature half of the token fails verification with 422."""
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    _install_detect_mock(monkeypatch, detected)
    _install_batch_mock(monkeypatch)

    body = _read_fixture("chase_sample.csv")
    pre = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("chase.csv", body, "text/csv")},
        data={"card_id": card_a},
    )
    token = pre.json()["import_token"]
    # Flip the last hex char of the signature half.
    payload_b64, sig = token.split(".", 1)
    tampered_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    tampered = f"{payload_b64}.{tampered_sig}"

    commit = http_client.post(
        "/imports/csv/commit",
        headers=_auth(user_a),
        files={"file": ("chase.csv", body, "text/csv")},
        data={
            "card_id": card_a,
            "import_token": tampered,
            "column_mapping": json.dumps(detected.model_dump()),
        },
    )
    assert commit.status_code == 422
    assert commit.json()["detail"]["code"] == "invalid_import_token"


def test_commit_rejects_mismatched_file(
    http_client, user_a, card_a, clean_transactions, monkeypatch
):
    """Uploading a different file at /commit than at /preview fails 422."""
    detected = ColumnMapping(
        date="Transaction Date",
        merchant="Description",
        amount="Amount",
        currency=None,
        confidence=0.95,
    )
    _install_detect_mock(monkeypatch, detected)
    _install_batch_mock(monkeypatch)

    body_a = _read_fixture("chase_sample.csv")
    body_b = _read_fixture("amex_sample.csv")
    pre = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user_a),
        files={"file": ("chase.csv", body_a, "text/csv")},
        data={"card_id": card_a},
    )
    token = pre.json()["import_token"]

    commit = http_client.post(
        "/imports/csv/commit",
        headers=_auth(user_a),
        files={"file": ("amex.csv", body_b, "text/csv")},
        data={
            "card_id": card_a,
            "import_token": token,
            "column_mapping": json.dumps(detected.model_dump()),
        },
    )
    assert commit.status_code == 422
    assert commit.json()["detail"]["code"] == "invalid_import_token"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Auth headers helper — mirrors test_chat_stream.py."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _parse_sse(body: bytes) -> list[tuple[str, str]]:
    """Parse SSE wire bytes into `[(event, data), ...]` tuples."""
    frames: list[tuple[str, str]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for raw_line in body.decode("utf-8").split("\n"):
        line = raw_line.rstrip("\r")
        if line == "":
            if current_event is not None or current_data:
                frames.append((current_event or "message", "\n".join(current_data)))
            current_event = None
            current_data = []
        elif line.startswith("event:"):
            current_event = line[len("event:"):].lstrip(" ")
        elif line.startswith("data:"):
            current_data.append(line[len("data:"):].lstrip(" "))
    return frames


def _read_fixture(name: str) -> bytes:
    """Read a fixture CSV as raw bytes."""
    return (FIXTURE_DIR / name).read_bytes()


def _install_detect_mock(
    monkeypatch,
    mapping: ColumnMapping,
    calls: list | None = None,
) -> None:
    """Patch detect_columns at the route's import site.

    Optionally append `(headers, sample_rows)` to `calls` so a test can
    assert what Gemini was asked.
    """

    def _fake_detect(headers, sample_rows, user):
        """Provide a deterministic ColumnMapping for tests."""
        if calls is not None:
            calls.append((headers, sample_rows))
        # Still write the ai_call_log row so the per-call accounting
        # assertions stay realistic.
        from app.integrations.aicalllog import log_ai_call

        log_ai_call(
            user.jwt,
            user_id=user.user_id,
            provider="google",
            model="mocked-flash",
            task_type="csv_import",
            prompt_version="csv_detect_v1",
            prompt_hash="x" * 64,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            success=True,
            error_code=None,
        )
        return mapping

    monkeypatch.setattr(imports_module, "detect_columns", _fake_detect)


def _install_batch_mock(
    monkeypatch,
    category: str = "Dining",
    confidence: float = 0.9,
    batch_calls: list | None = None,
) -> None:
    """Patch categorize_batch at the route's import site.

    Returns `category` for every row. Append `(rows, past_corrections)`
    to `batch_calls` for per-call assertions.
    """

    def _fake_batch(rows, past_corrections, user):
        """Provide a deterministic categorization for every row."""
        if batch_calls is not None:
            batch_calls.append((list(rows), list(past_corrections)))
        from app.integrations.aicalllog import log_ai_call

        log_ai_call(
            user.jwt,
            user_id=user.user_id,
            provider="google",
            model="mocked-flash",
            task_type="csv_import",
            prompt_version="csv_batch_v1",
            prompt_hash="y" * 64,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            success=True,
            error_code=None,
        )
        return [CategorySuggestion(category=category, confidence=confidence)] * len(rows)

    monkeypatch.setattr(imports_module, "categorize_batch", _fake_batch)


def _ai_call_log_today(user, admin_client) -> list[dict]:
    """Fetch user's ai_call_log rows from today, descending by timestamp."""
    today = _dt.datetime.now(_dt.timezone.utc).date()
    midnight = _dt.datetime.combine(today, _dt.time.min, tzinfo=_dt.timezone.utc)
    resp = (
        admin_client.table("ai_call_log")
        .select("*")
        .eq("user_id", user.id)
        .gte("timestamp", midnight.isoformat())
        .order("timestamp", desc=True)
        .execute()
    )
    return resp.data or []


def _do_preview_then_commit(
    http_client,
    user,
    card_id,
    fixture_name: str,
    detected: ColumnMapping,
    monkeypatch,
    batch_category: str = "Dining",
) -> tuple[int, list[tuple[str, str]]]:
    """Run /preview followed by /commit and return `(status, frames)`."""
    _install_detect_mock(monkeypatch, detected)
    _install_batch_mock(monkeypatch, category=batch_category)

    body = _read_fixture(fixture_name)
    pre = http_client.post(
        "/imports/csv/preview",
        headers=_auth(user),
        files={"file": (fixture_name, body, "text/csv")},
        data={"card_id": card_id},
    )
    assert pre.status_code == 200, pre.text
    token = pre.json()["import_token"]

    commit = http_client.post(
        "/imports/csv/commit",
        headers=_auth(user),
        files={"file": (fixture_name, body, "text/csv")},
        data={
            "card_id": card_id,
            "import_token": token,
            "column_mapping": json.dumps(detected.model_dump()),
        },
    )
    return commit.status_code, _parse_sse(commit.content)
