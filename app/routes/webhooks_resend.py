"""Resend bounce/complaint webhook (DESIGN.md §6.4).

Resend POSTs delivery events here (signed by Svix). Three event types
matter for v1:

  - `email.bounced` with `bounce.type == 'hard'`  → suppress the user.
  - `email.complained`                             → suppress the user.
  - `email.delivery_delayed`                       → log only.

Soft bounces are not surfaced — Resend retries internally. Suppressing
a user on a transient blip is exactly wrong.

NO AUTH on this route; Svix signature verification IS the
authorization. A missing/invalid signature returns 400; everything
else returns 200 even on internal error so Resend doesn't retry-storm
us into noise.

SERVICE ROLE in this file. The webhook has no user JWT in scope
(Resend doesn't know about our auth). CLAUDE.md invariant 1 lists
this file as the fourth sanctioned service-role caller; the
`tests/contracts/test_no_service_role_leak.py` per-file allowlist
exempts it from the directory-only exclusion rule.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from app.db import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/resend")
async def resend_webhook(request: Request) -> PlainTextResponse:
    """Process one Resend webhook event.

    Verifies the Svix signature against `RESEND_WEBHOOK_SECRET`. On
    valid bounce/complaint events, flips the affected user's
    `weekly_digest_enabled` to false and stamps `bounce_type` on the
    matching `email_log` row. Unknown event types are ignored (200);
    unknown message ids are ignored (200) because we may receive
    webhooks for sends from a future welcome-sequence kind that
    bypasses suppression in v1.
    """
    raw_body = await request.body()
    secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    if not secret:
        # Fail loud rather than silently accept unsigned webhooks. This
        # is also what the `_REQUIRED_ENV_VARS` boot check is supposed
        # to prevent — defense in depth.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RESEND_WEBHOOK_SECRET not configured",
        )

    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        # Lazy import — svix is only needed for this route, and keeping
        # the import lazy means tests can run without the package
        # installed in environments where the dep isn't yet present.
        from svix.webhooks import Webhook, WebhookVerificationError

        payload = Webhook(secret).verify(raw_body, headers)
    except WebhookVerificationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid signature",
        )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="svix package not installed",
        )

    if isinstance(payload, (bytes, str)):
        payload = json.loads(payload)
    event_type = payload.get("type")
    data = payload.get("data") or {}

    if event_type == "email.bounced":
        bounce = (data.get("bounce") or {})
        if bounce.get("type") == "hard":
            _suppress(data.get("email_id"), bounce_type="hard")
    elif event_type == "email.complained":
        _suppress(data.get("email_id"), bounce_type="complaint")
    elif event_type == "email.delivery_delayed":
        logger.info(
            "resend delivery delayed (no action)",
            extra={"message_id": data.get("email_id")},
        )
    else:
        # Unknown event type: 200 no-op. Resend ships new event types
        # periodically; we don't want a new type to retry-storm us.
        logger.info("resend webhook unhandled event", extra={"event_type": event_type})

    return PlainTextResponse("", status_code=200)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _suppress(message_id: str | None, *, bounce_type: str) -> None:
    """Flip `weekly_digest_enabled=false` for the user behind `message_id`.

    Looks up the matching `email_log` row by `provider_message_id`,
    stamps `bounce_type`, then flips the user's preference column. If
    the message id is unknown (race against a future welcome-sequence
    kind, or a duplicate webhook after row cleanup), this is a 200
    no-op — the webhook handler always returns 200.
    """
    if not message_id:
        return
    admin = supabase_admin()
    log_resp = (
        admin.table("email_log")
        .select("user_id")
        .eq("provider_message_id", message_id)
        .limit(1)
        .execute()
    )
    if not log_resp.data:
        logger.info(
            "resend webhook for unknown message_id",
            extra={"message_id": message_id, "bounce_type": bounce_type},
        )
        return
    user_id = log_resp.data[0]["user_id"]

    admin.table("email_log").update({"bounce_type": bounce_type}).eq(
        "provider_message_id", message_id
    ).execute()

    admin.table("users_meta").update({"weekly_digest_enabled": False}).eq(
        "user_id", user_id
    ).execute()
    logger.info(
        "user suppressed via resend webhook",
        extra={"user_id": user_id, "bounce_type": bounce_type},
    )
