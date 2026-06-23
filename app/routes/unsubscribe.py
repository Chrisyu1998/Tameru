"""One-click List-Unsubscribe routes (DESIGN.md §6.4, RFC 8058).

Two routes with different mutation semantics (audit P3-10):

  - `GET /unsubscribe` — the human-facing path (visible body link).
    Verifies the HMAC token and 302-redirects to the PWA's
    `/unsubscribe` confirm page WITHOUT mutating. A GET that mutated on
    first fetch silently unsubscribed users whose corporate mail
    scanners (Outlook SafeLinks, Mimecast) prefetch every link in the
    email body — no human ever clicked.
  - `POST /unsubscribe` — the mutation. Called by Gmail/Yahoo's
    automated one-click flow (RFC 8058 / `List-Unsubscribe-Post:
    List-Unsubscribe=One-Click`) and by the PWA confirm page's button.
    Scanners don't POST RFC 8058 endpoints; a POST is intent.

NO AUTH on these routes. The HMAC token IS the authorization — a
forged token gets a 403; a valid token authorizes the opt-out without
the user having to sign in. That's the entire point of one-click.

SERVICE ROLE in this file. The flip happens without a user JWT in
scope (Gmail can't carry the user's session JWT through a webhook-style
POST), so the column flip uses the admin client. This file is in the
per-file allowlist of `tests/contracts/test_no_service_role_leak.py`
with a rationale comment — that test now skips both this file and
`app/routes/webhooks_resend.py` because both are CLAUDE.md invariant 1
sanctioned service-role callers (the request has no user JWT in scope).
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.db import supabase_admin
from app.util.unsubscribe import UnsubscribeKind, verify_unsubscribe_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["unsubscribe"])


@router.get("/unsubscribe")
def unsubscribe_get(
    user: UUID = Query(..., alias="user"),
    kind: str = Query(..., alias="kind"),
    token: str = Query(..., alias="token"),
) -> RedirectResponse:
    """Redirect a human click to the PWA confirm page — never mutate on GET.

    Corporate link scanners GET every URL in an email body, so a
    mutating GET silently unsubscribed scanned users (audit P3-10). The
    token is verified here first — a forged link 403s without bouncing
    the visitor through the PWA — then forwarded intact in the redirect
    so the confirm page's button can POST it back. The redirect target
    is the fixed frontend origin, never derived from request input (no
    open-redirect surface).
    """
    validated_kind = _validated_kind(kind)
    if not verify_unsubscribe_token(token, user, validated_kind):
        # Don't leak why (forged token vs rotated secret vs wrong
        # user) — the only legitimate caller has a valid token.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid token")
    query = urlencode({"user": str(user), "kind": validated_kind, "token": token})
    return RedirectResponse(
        f"{_frontend_origin()}/unsubscribe?{query}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/unsubscribe")
def unsubscribe_post(
    user: UUID = Query(..., alias="user"),
    kind: str = Query(..., alias="kind"),
    token: str = Query(..., alias="token"),
) -> Response:
    """RFC 8058 one-click POST — the single mutation path.

    Gmail and Yahoo's automated unsubscribe flow POSTs to the URL in
    the `List-Unsubscribe` header (when paired with
    `List-Unsubscribe-Post: List-Unsubscribe=One-Click`), and the PWA
    `/unsubscribe` confirm page's button POSTs here too (a simple
    cross-origin request — no auth header, no preflight; the HMAC token
    is the authorization). Spec says return 200 with an empty body.
    """
    _apply_unsubscribe(user, kind, token)
    return PlainTextResponse("", status_code=200)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _apply_unsubscribe(user_id: UUID, kind_raw: str, token: str) -> None:
    """Verify the HMAC token and flip the column.

    Service-role write (no user JWT in scope — that's the whole point
    of one-click). RLS bypassed; the function authorizes via HMAC.
    """
    kind = _validated_kind(kind_raw)
    if not verify_unsubscribe_token(token, user_id, kind):
        # Don't leak why (forged token vs rotated secret vs wrong
        # user) — the only legitimate caller has a valid token.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid token")

    admin = supabase_admin()
    admin.table("users_meta").update({"weekly_digest_enabled": False}).eq(
        "user_id", str(user_id)
    ).execute()
    logger.info(
        "user unsubscribed via one-click",
        extra={"user_id": str(user_id), "kind": kind},
    )


def _frontend_origin() -> str:
    """The PWA origin the GET redirect targets.

    `FRONTEND_ORIGIN` is in the production-required env tier (app/main.py
    lifespan); the localhost fallback keeps dev working where the var is
    deliberately unset (.env.example documents it commented-out).
    """
    return (os.environ.get("FRONTEND_ORIGIN") or "http://localhost:5173").rstrip("/")


def _validated_kind(raw: str) -> UnsubscribeKind:
    """Reject unknown unsubscribe kinds at the route boundary."""
    if raw == "digest":
        return "digest"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"unknown unsubscribe kind: {raw}",
    )
