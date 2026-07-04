"""Unit + integration tests for `parse_receipt` (Gemini Vision receipt path).

Mirrors `tests/integrations/test_categorize.py`'s fake-Gemini pattern: the SDK's
`generate_content` is mocked so no real Gemini call happens, but the
`ai_call_log` write goes through the real Supabase path under the user's JWT —
which is what verifies the `20260704120000` migration end to end (a bad
`task_type` would raise `AICallLogError` on insert, so a green row proves
`receipt_parse` is back in the CHECK).
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.integrations import gemini as gemini_module
from app.integrations.gemini import (
    GeminiProviderError,
    ReceiptExtraction,
    parse_receipt,
)
from app.prompts.receipt import RECEIPT_PROMPT_VERSION

# Minimal JPEG-ish bytes — `types.Part.from_bytes` just wraps them; the
# mocked generate_content never inspects the image.
_IMG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


@pytest.fixture
def authed_user(user_a) -> AuthedUser:
    """Adapt conftest's TestUser (str id) to AuthedUser (UUID user_id)."""
    return AuthedUser(jwt=user_a.jwt, user_id=UUID(user_a.id), email=user_a.email)


@dataclass
class _FakeUsage:
    """Mimic the SDK's usage_metadata shape."""

    prompt_token_count: int
    candidates_token_count: int


@dataclass
class _FakeResponse:
    """Mimic the SDK's generate_content response (only .text + usage read)."""

    text: str
    usage_metadata: _FakeUsage


def test_parse_receipt_happy_path(authed_user, monkeypatch):
    """A well-formed response maps to a fully-populated ReceiptExtraction."""
    _install_fake_gemini(
        monkeypatch,
        return_value=_fake_response(
            {
                "merchant": "Trader Joe's",
                "amount": "47.02",
                "date": "2026-07-01",
                "currency": "USD",
            }
        ),
    )

    result = parse_receipt(_IMG, "image/jpeg", authed_user)

    assert isinstance(result, ReceiptExtraction)
    assert result.merchant == "Trader Joe's"
    assert result.amount == Decimal("47.02")
    assert result.date == _dt.date(2026, 7, 1)
    assert result.currency == "USD"


def test_parse_receipt_writes_receipt_parse_log_row(authed_user, monkeypatch):
    """One ai_call_log row lands with task_type='receipt_parse'.

    This is the migration guard: `receipt_parse` was dropped from the CHECK in
    20260522130000 and re-added in 20260704120000 — if the re-add were missing,
    the INSERT would raise AICallLogError and this test would error, not xfail.
    """
    _install_fake_gemini(
        monkeypatch,
        return_value=_fake_response(
            {
                "merchant": "Blue Bottle",
                "amount": "6.50",
                "date": "2026-07-02",
                "currency": "USD",
            },
            input_tokens=1234,
            output_tokens=20,
        ),
    )

    parse_receipt(_IMG, "image/jpeg", authed_user)

    rows = _receipt_log_rows_for(authed_user)
    assert rows, "expected a receipt_parse ai_call_log row"
    row = rows[0]
    assert row["provider"] == "google"
    assert row["task_type"] == "receipt_parse"
    assert row["prompt_version"] == RECEIPT_PROMPT_VERSION
    assert row["success"] is True
    assert row["error_code"] is None
    assert row["input_tokens"] == 1234
    assert row["output_tokens"] == 20
    assert row["latency_ms"] is not None


def test_parse_receipt_empty_fields_become_none(authed_user, monkeypatch):
    """A non-receipt image returns empty strings → all None.

    The route turns a None merchant/amount into a 422; here we just pin the
    normalization boundary.
    """
    _install_fake_gemini(
        monkeypatch,
        return_value=_fake_response(
            {"merchant": "", "amount": "", "date": "", "currency": ""}
        ),
    )

    result = parse_receipt(_IMG, "image/jpeg", authed_user)

    assert result.merchant is None
    assert result.amount is None
    assert result.date is None
    assert result.currency is None


def test_parse_receipt_bad_amount_and_date_degrade(authed_user, monkeypatch):
    """Unparseable amount → None; malformed date → None; merchant survives.

    A readable total with an unreadable date is still useful (date defaults to
    the user's local today downstream), so a bad date must not sink the parse.
    """
    _install_fake_gemini(
        monkeypatch,
        return_value=_fake_response(
            {
                "merchant": "Corner Deli",
                "amount": "N/A",
                "date": "07/03/2026",  # not ISO — degrades to None
                "currency": "usd",
            }
        ),
    )

    result = parse_receipt(_IMG, "image/jpeg", authed_user)

    assert result.merchant == "Corner Deli"
    assert result.amount is None
    assert result.date is None
    assert result.currency == "USD"


def test_parse_receipt_negative_amount_degrades_to_none(authed_user, monkeypatch):
    """A non-positive total is treated as unreadable (None), not stored."""
    _install_fake_gemini(
        monkeypatch,
        return_value=_fake_response(
            {"merchant": "Refund Co", "amount": "-9.99", "date": "", "currency": "USD"}
        ),
    )

    result = parse_receipt(_IMG, "image/jpeg", authed_user)

    assert result.amount is None


@pytest.mark.parametrize("bad_amount", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_parse_receipt_non_finite_amount_degrades_without_raising(
    authed_user, monkeypatch, bad_amount
):
    """`Decimal` accepts NaN/Infinity, and `NaN > 0` raises — the guard must
    treat non-finite totals as unreadable (None) rather than letting the
    comparison escape as an unhandled 500."""
    _install_fake_gemini(
        monkeypatch,
        return_value=_fake_response(
            {"merchant": "Glitch Mart", "amount": bad_amount, "date": "", "currency": "USD"}
        ),
    )

    # Must not raise (the whole point of the fix).
    result = parse_receipt(_IMG, "image/jpeg", authed_user)

    assert result.merchant == "Glitch Mart"
    assert result.amount is None


def test_parse_receipt_logs_failure_on_provider_error(authed_user, monkeypatch):
    """An SDK error is classified to a GeminiError and logged as a failure row
    (task_type='receipt_parse') before re-raising."""
    _install_fake_gemini(monkeypatch, side_effect=RuntimeError("simulated SDK blowup"))

    with pytest.raises(GeminiProviderError):
        parse_receipt(_IMG, "image/jpeg", authed_user)

    row = _receipt_log_rows_for(authed_user)[0]
    assert row["success"] is False
    assert row["task_type"] == "receipt_parse"
    assert row["error_code"] == "provider_error"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _fake_response(
    payload: dict | str,
    *,
    input_tokens: int = 900,
    output_tokens: int = 18,
) -> _FakeResponse:
    """Build a fake generate_content response carrying JSON `text`."""
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return _FakeResponse(
        text=body,
        usage_metadata=_FakeUsage(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
        ),
    )


def _install_fake_gemini(monkeypatch, *, return_value=None, side_effect=None):
    """Monkeypatch `_gemini_client()` to return a MagicMock whose
    `models.generate_content(...)` does what the test needs."""
    client = MagicMock()
    if side_effect is not None:
        client.models.generate_content.side_effect = side_effect
    else:
        client.models.generate_content.return_value = return_value
    monkeypatch.setattr(gemini_module, "_gemini_client", lambda: client)
    monkeypatch.setattr(gemini_module, "_client", None)
    return client


def _receipt_log_rows_for(user: AuthedUser):
    """Read back the test user's receipt_parse rows, newest first. RLS-scoped."""
    client = supabase_for_user(user.jwt)
    return (
        client.table("ai_call_log")
        .select(
            "provider, model, task_type, prompt_version, prompt_hash, "
            "input_tokens, output_tokens, latency_ms, success, error_code"
        )
        .eq("user_id", str(user.user_id))
        .eq("task_type", "receipt_parse")
        .order("timestamp", desc=True)
        .execute()
        .data
        or []
    )
