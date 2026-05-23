"""Thin Resend SDK wrapper for transactional email (DESIGN.md §6.4).

A pure boundary adapter: takes resolved arguments, returns a normalized
result, swallows nothing. The caller is responsible for logging to
`email_log` because only the caller knows the cron context (which user,
which kind, what week boundary). Keeping this wrapper unaware of
`email_log` keeps it reusable for any future scheduled email (welcome
sequence, §16).

DELIVERABILITY PROPERTIES set here (not configurable per call):

  - Always both HTML and plaintext. Spam filters score HTML/text
    similarity; the plaintext is required.
  - `List-Unsubscribe` header carries BOTH a URL and a mailto, per RFC
    2369. The companion `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
    header (RFC 8058) tells Gmail/Yahoo "POST that URL to unsubscribe
    without further user interaction." We ship this below the Gmail 5K/day
    threshold deliberately — inbox placement benefits even at v1 scale.

PRIVACY PROPERTIES that must be configured in the Resend dashboard, not
here: open and click tracking must be DISABLED for the project. Open
tracking is a 1px pixel that exfiltrates recipient IP on every email
open; click tracking rewrites every link through resend.com. Both
violate the Tameru privacy posture (CLAUDE.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import resend


@dataclass(frozen=True)
class ResendSendResult:
    """Outcome of a single `resend.Emails.send` call.

    `message_id` is Resend's `id` field on the response — the join key
    for the bounce/complaint webhook to flip the right `email_log` row's
    `bounce_type` and the right user's `weekly_digest_enabled`. Nullable
    only when `success=false`.
    """
    message_id: str | None
    success: bool
    error_code: str | None


def send_digest_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    list_unsubscribe_url: str,
    list_unsubscribe_mailto: str,
    idempotency_key: str,
    from_address: str | None = None,
    reply_to: str | None = None,
) -> ResendSendResult:
    """Send a single digest email via Resend.

    Args:
      to: Recipient address (the digest is one-to-one; no list semantics).
      subject: Email subject line. Keep terse — Gmail truncates after
        ~70 chars in the inbox list.
      html: Rendered HTML body with inline styles only (Gmail strips
        <style> blocks and Tailwind class names).
      text: Rendered plaintext body. Must be a real readable version,
        not a "view in browser" stub — spam filters score similarity.
      list_unsubscribe_url: Per-user HMAC-tokenized URL for one-click
        unsubscribe. Sent BOTH as the body's visible link and the
        List-Unsubscribe header URL.
      list_unsubscribe_mailto: mailto: address with the unsubscribe
        token encoded in the subject query param. RFC 2369 best
        practice is to ship both transport options.
      idempotency_key: Per-(recipient, week) stable key sent in the
        `Idempotency-Key` HTTP header. Resend dedupes by this key for
        ~24h on their side: if urllib3 retries the POST after a
        transient TCP error (the SDK's default behavior, which we
        don't control and which doesn't coordinate with our DB
        reservation), Resend returns the cached response instead of
        creating a second send. This closes the in-flight-retry gap
        the partial unique index can't see. Required, not optional —
        a missing key would silently re-open the gap.
      from_address: Override the default `RESEND_FROM` env (mostly for
        tests). Production sends from `"Tameru" <hello@mail.tameru.app>`.
      reply_to: Override the default `RESEND_REPLY_TO` env. Production
        routes replies to a real inbox so user feedback reaches a human.

    Returns:
      ResendSendResult — `success=True` with a message_id on accepted
      sends; `success=False` with an error_code on any SDK exception.
      Never raises — the cron loop must continue past a single user's
      failure.
    """
    from_value = from_address or os.environ.get("RESEND_FROM") or "Tameru <hello@mail.tameru.app>"
    reply_to_value = reply_to or os.environ.get("RESEND_REPLY_TO") or "hello@mail.tameru.app"

    params: resend.Emails.SendParams = {
        "from": from_value,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
        "reply_to": reply_to_value,
        # `headers` here are CUSTOM EMAIL HEADERS attached to the
        # outgoing MIME message (the recipient's mail client sees them).
        # `Idempotency-Key` is NOT one of these — it must go through
        # SendOptions below as an HTTP request header to Resend's API.
        "headers": {
            "List-Unsubscribe": f"<{list_unsubscribe_url}>, <{list_unsubscribe_mailto}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }
    # SendOptions is the SDK's surface for HTTP-request-level options.
    # The `idempotency_key` field maps to the `Idempotency-Key` HTTP
    # header sent to api.resend.com — Resend dedupes by it for ~24h on
    # their side. Placing it inside `params["headers"]` (a previous bug)
    # would have made it a custom email header instead, which Resend
    # would NOT dedupe on, defeating the entire Layer-2 guarantee.
    options: resend.Emails.SendOptions = {"idempotency_key": idempotency_key}

    try:
        # _require_env is inside the try so a missing RESEND_API_KEY
        # surfaces as success=False rather than raising up to the cron.
        # Raising would land in the cron's "wrapper contract violation"
        # branch — which holds the slot conservatively, leaving the user
        # stuck until manual ops clears the row. Treating it as a
        # rejected send releases the slot so a deploy that supplies the
        # missing key can retry within the same week.
        resend.api_key = _require_env("RESEND_API_KEY")
        response = resend.Emails.send(params, options)
    except Exception as exc:
        return ResendSendResult(
            message_id=None,
            success=False,
            error_code=type(exc).__name__,
        )
    message_id = response.get("id") if isinstance(response, dict) else None
    return ResendSendResult(message_id=message_id, success=True, error_code=None)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    """Read an env var or raise — fail-loud on the cron path, not silent."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. See .env.example.")
    return value
