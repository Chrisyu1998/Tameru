"""Route tests for `POST /receipts/parse` (receipt photo → transaction proposal).

`parse_receipt` (Gemini Vision) and `categorize` (Gemini) are both mocked — the
route's own logic is what's under test: image validation, the
`source='receipt_photo'` threading, and provider-error → 503. The source
round-trip is verified by posting the returned proposal to
`POST /transactions/confirm` and asserting the committed row's `source`.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.integrations.gemini import (
    CategorySuggestion,
    GeminiProviderError,
    ReceiptExtraction,
)
from app.main import app
from app.routes import receipts as receipts_module
from app.services import transactions as transactions_module


@pytest.fixture
def client() -> TestClient:
    """Provide a TestClient (bare form — does not enter the lifespan)."""
    return TestClient(app)


def test_parse_returns_receipt_photo_proposal(client, user_a, monkeypatch):
    """Happy path: a scanned receipt returns a source='receipt_photo' proposal
    with the category filled by the normal categorize() path and no card."""
    _stub_vision(monkeypatch)

    resp = client.post("/receipts/parse", headers=_auth(user_a), files=_img())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merchant"] == "Trader Joe's"
    assert body["source"] == "receipt_photo"
    assert body["category"] == "Groceries"
    assert body["gemini_suggestion"] == "Groceries"
    assert body["card_id"] is None
    assert body["client_request_id"]
    assert Decimal(str(body["amount"])) == Decimal("47.02")


def test_receipt_proposal_commits_with_receipt_photo_source(
    client, user_a, monkeypatch
):
    """The proposal round-trips through POST /transactions/confirm and the
    committed row carries source='receipt_photo' (the confirm route honors
    proposal.source instead of hardcoding 'nlp')."""
    _stub_vision(monkeypatch)

    parse = client.post("/receipts/parse", headers=_auth(user_a), files=_img()).json()
    confirm = client.post(
        "/transactions/confirm", headers=_auth(user_a), json=parse
    )

    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["transaction"]["source"] == "receipt_photo"


def test_parse_gemini_failure_returns_503_not_500(client, user_a, monkeypatch):
    """A Gemini Vision outage surfaces as a CORS-safe 503 with the error code,
    not the catch-all 500 (which would ship without a legible code)."""

    def _boom(image_bytes, mime_type, user):
        """Simulate a provider failure inside parse_receipt."""
        raise GeminiProviderError("gemini down")

    monkeypatch.setattr(receipts_module, "parse_receipt", _boom)

    resp = client.post("/receipts/parse", headers=_auth(user_a), files=_img())

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "provider_error"


def test_parse_unsupported_mime_returns_422(client, user_a, monkeypatch):
    """A non-image upload is rejected before any Gemini call."""
    _stub_vision(monkeypatch)

    resp = client.post(
        "/receipts/parse",
        headers=_auth(user_a),
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsupported_image"


def test_parse_unreadable_receipt_returns_422(client, user_a, monkeypatch):
    """When Gemini can't read a merchant + total, the route 422s rather than
    proposing a junk card."""
    _stub_vision(monkeypatch, merchant=None, amount=None)

    resp = client.post("/receipts/parse", headers=_auth(user_a), files=_img())

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unreadable_receipt"


def test_parse_requires_auth(client):
    """No JWT → 401 (device-gated write path)."""
    resp = client.post("/receipts/parse", files=_img())
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _auth(user) -> dict[str, str]:
    """Bearer + device headers, as every device-gated route requires."""
    return {
        "Authorization": f"Bearer {user.jwt}",
        "X-Device-Id": user.device_id or "",
    }


def _img(
    content: bytes = b"\xff\xd8\xffjpegbytes",
    mime: str = "image/jpeg",
    name: str = "receipt.jpg",
) -> dict:
    """Build a multipart `files=` dict for the receipt upload field."""
    return {"file": (name, io.BytesIO(content), mime)}


def _stub_vision(
    monkeypatch,
    *,
    merchant: str | None = "Trader Joe's",
    amount: Decimal | None = Decimal("47.02"),
) -> None:
    """Neutralize both Gemini calls: parse_receipt (vision) returns a fixed
    extraction, and categorize (used by build_transaction_proposal) returns a
    fixed category — so the route runs end-to-end with no network."""

    def _parse(image_bytes, mime_type, user):
        """Return a canned ReceiptExtraction."""
        return ReceiptExtraction(
            merchant=merchant,
            amount=amount,
            date=date(2026, 7, 1),
            currency="USD",
        )

    monkeypatch.setattr(receipts_module, "parse_receipt", _parse)
    monkeypatch.setattr(
        transactions_module,
        "categorize",
        lambda merchant, user: CategorySuggestion(category="Groceries", confidence=0.9),
    )
