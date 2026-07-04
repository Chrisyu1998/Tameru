"""Receipt-photo endpoint — snap a receipt, get a transaction proposal.

`POST /receipts/parse` is the Gemini-Vision sibling of the CSV-import path,
but with a chat-composer entry point. A photo is uploaded (the frontend
downscales + JPEG-re-encodes first), Gemini Vision extracts merchant / amount
/ date, and the route returns a standard `TransactionProposal`
(source='receipt_photo').

The image never touches the Haiku chat agent loop and is never stored — it is a
request-local `bytes` value, discarded after the Gemini call (privacy posture,
DESIGN.md §9). The proposal is committed by the existing
`POST /transactions/confirm` (the parse card the frontend renders from this
response), so idempotency, the entry-moment insight, and the merchant-correction
learning loop are all inherited unchanged — there is no receipt-specific commit
path (invariant 8: the HTTP confirm is the commit, not a `tool_use` write).

RLS fires on every read/write via the caller's JWT; the service role is never
used (invariant 1). The one Gemini Vision call is logged to `ai_call_log` with
`task_type='receipt_parse'` under the caller's JWT (invariant 14).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.auth import AuthedUser, get_current_user_with_device
from app.integrations.gemini import GeminiError, parse_receipt
from app.models.transactions import TransactionProposal
from app.services.transactions import build_transaction_proposal

router = APIRouter(prefix="/receipts", tags=["receipts"])

# Backstop cap. The frontend downscales + re-encodes to JPEG (typically
# < ~500 KB) before upload, so this guards a direct/oversized POST rather than
# being the primary size control. 10 MB comfortably covers one un-downscaled
# phone photo.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Image types Gemini Vision accepts. The frontend always sends image/jpeg; the
# wider set keeps a direct API caller (or a browser that skipped the canvas
# re-encode) working.
_SUPPORTED_IMAGE_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
)


@router.post("/parse", response_model=TransactionProposal)
def parse_receipt_photo(
    file: UploadFile = File(...),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> TransactionProposal:
    """Extract a transaction proposal from a receipt photo.

    Request: multipart `file` — a receipt image (image/jpeg preferred; png /
    webp / heic / heif accepted).

    Response: a `TransactionProposal` (source='receipt_photo'); the client
    renders it as a parse card and posts it to `POST /transactions/confirm`
    after the user taps "looks right". The card carries no `card_id` — a
    receipt can't know which card paid, so the user assigns one via the edit
    sheet if they want.

    422: not an image / unsupported type, or Gemini couldn't read a merchant
         and total (not a receipt, unreadable total).
    413: image larger than the backstop cap.
    503: Gemini Vision upstream failure.
    """
    mime_type = _image_mime_type(file)
    image_bytes = _read_image_bytes(file)

    try:
        extraction = parse_receipt(image_bytes, mime_type, user)
    except GeminiError as exc:
        raise _provider_error(exc) from exc

    # The load-bearing pair. Without a merchant AND a total there is nothing to
    # propose — surface a clear 422 rather than a junk card the user has to
    # fully rewrite. `date` may still be None (defaults to local today in the
    # builder); `currency` is advisory only (no FX, invariant 13).
    if extraction.merchant is None or extraction.amount is None:
        raise _domain_error(
            "unreadable_receipt",
            "couldn't read a merchant and total from that photo — try a "
            "clearer shot, or add it by typing.",
        )

    return build_transaction_proposal(
        user,
        merchant=extraction.merchant,
        amount=extraction.amount,
        date=extraction.date,
        card_id=None,
        category=None,
        notes=None,
        source="receipt_photo",
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _image_mime_type(file: UploadFile) -> str:
    """Return the upload's normalized image MIME type, or raise 422.

    Strips any `; charset=` parameter and lowercases. Rejects non-image and
    non-Gemini-supported types up front so the user gets a clear 422 instead of
    an opaque provider error after the upload round-trips to Gemini.
    """
    raw = (file.content_type or "").split(";", 1)[0].strip().lower()
    if raw not in _SUPPORTED_IMAGE_TYPES:
        raise _domain_error(
            "unsupported_image",
            "upload must be a JPEG, PNG, WebP, or HEIC image",
        )
    return raw


def _read_image_bytes(file: UploadFile) -> bytes:
    """Read the upload, rejecting oversized/empty payloads before the Gemini call.

    Two-stage size check (mirrors the CSV-import path): trust `UploadFile.size`
    when present, then re-check the actual read length so a missing or lying
    Content-Length still fails closed at the cap.
    """
    declared = getattr(file, "size", None)
    if declared is not None and declared > _MAX_IMAGE_BYTES:
        raise _too_large(f"image is {declared} bytes; max is {_MAX_IMAGE_BYTES}")
    data = file.file.read(_MAX_IMAGE_BYTES + 1)
    if len(data) > _MAX_IMAGE_BYTES:
        raise _too_large(f"image exceeds {_MAX_IMAGE_BYTES} bytes")
    if not data:
        raise _domain_error("empty_image", "the uploaded image was empty")
    return data


def _domain_error(code: str, message: str) -> HTTPException:
    """422 with the project's standard `{code, message}` body."""
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _too_large(message: str) -> HTTPException:
    """413 with the standard body shape (int pinned per the imports.py note)."""
    return HTTPException(
        status_code=413,
        detail={"code": "payload_too_large", "message": message},
    )


def _provider_error(exc: GeminiError) -> HTTPException:
    """503 surface for an upstream Gemini Vision failure.

    An `HTTPException` flows back out through `CORSMiddleware` (unlike an
    unhandled exception, which the catch-all 500 synthesizes *outside* it), so
    the cross-origin PWA sees a real error code instead of a bare "Load failed".
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": exc.error_code, "message": str(exc)},
    )
