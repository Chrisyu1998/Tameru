"""One-click List-Unsubscribe routes (DESIGN.md §6.4, RFC 8058).

Two routes: `GET /unsubscribe` for users who click the visible body
link or the Gmail "Unsubscribe" button (which sends GET with the URL
from the `List-Unsubscribe` header), and `POST /unsubscribe` for Gmail
and Yahoo's automated one-click flow (RFC 8058 / `List-Unsubscribe-Post:
List-Unsubscribe=One-Click`).

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
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.db import supabase_admin
from app.util.unsubscribe import UnsubscribeKind, verify_unsubscribe_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["unsubscribe"])

_SUCCESS_PAGE = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Unsubscribed — Tameru</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      body{font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
           color:#1a1a1a;background:#fafafa;margin:0;padding:48px 24px;
           display:flex;justify-content:center}
      .card{max-width:420px;background:#fff;border:1px solid #eee;
            border-radius:12px;padding:32px}
      h1{font-size:20px;margin:0 0 8px 0;font-weight:600}
      p{margin:0;color:#555}
    </style>
  </head>
  <body>
    <div class="card">
      <h1>You're unsubscribed.</h1>
      <p>Tameru won't send you weekly digest emails anymore. You can
         re-enable them anytime in Settings → Notifications inside the
         app.</p>
    </div>
  </body>
</html>
"""


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_get(
    user: UUID = Query(..., alias="user"),
    kind: str = Query(..., alias="kind"),
    token: str = Query(..., alias="token"),
) -> HTMLResponse:
    """Handle a click on the visible body link or the Gmail Unsubscribe button.

    Flips `users_meta.weekly_digest_enabled` to false on success;
    returns 403 on a forged/tampered token. The success page is
    intentionally a static HTML response (no PWA shell load, no auth
    prompt) so the user sees confirmation immediately without round-
    tripping through the app's auth flow.
    """
    _apply_unsubscribe(user, kind, token)
    return HTMLResponse(_SUCCESS_PAGE, status_code=200)


@router.post("/unsubscribe")
def unsubscribe_post(
    user: UUID = Query(..., alias="user"),
    kind: str = Query(..., alias="kind"),
    token: str = Query(..., alias="token"),
) -> Response:
    """RFC 8058 one-click POST.

    Gmail and Yahoo's automated unsubscribe flow POSTs to the URL in
    the `List-Unsubscribe` header (when paired with
    `List-Unsubscribe-Post: List-Unsubscribe=One-Click`). Same effect
    as the GET; spec says return 200 with an empty body.
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


def _validated_kind(raw: str) -> UnsubscribeKind:
    """Reject unknown unsubscribe kinds at the route boundary."""
    if raw == "digest":
        return "digest"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"unknown unsubscribe kind: {raw}",
    )
