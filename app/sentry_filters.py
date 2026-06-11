"""Sentry `before_send` and `init` plumbing (DESIGN.md §14.5).

Three rules in the filter, in order:

1. Drop `HTTPException` *only when its status is 4xx*. 4xx is user input
   ("bad request"), not a backend bug — Sentry doesn't need to see it.
   A 5xx HTTPException is the app explicitly returning an error (e.g.
   `raise HTTPException(503, "Anthropic down")`) and IS a bug-class
   signal Sentry must keep.
2. Drop events whose origin module is an AI integration (`ai_call_log`
   already records those failures — §14.2 "don't double-log").
3. *Exception* to rule 2: `AICallLogError` always ships. That class
   means the AI call succeeded but the audit INSERT itself failed — the
   audit pipeline is broken, which is exactly what Sentry exists to
   catch. Dropping it would make the audit canary invisible.

After filtering, the event is tagged with the request `correlation_id`
(so a Sentry alert ties back to the same id in Railway stdout and the
`X-Request-ID` response header) and its payload is run through
`redact_mapping` so request bodies, query strings, `extra`, and
breadcrumb data carry no PII even if a downstream handler ignored the
redaction-at-log-time path.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from asgi_correlation_id import correlation_id

from app.logging_redaction import redact_mapping, redact_string

logger = logging.getLogger(__name__)

# Modules whose exceptions are routed to ai_call_log instead of Sentry.
# Anchored with a trailing dot so a future `app.integrations.gemini_x`
# would not accidentally match `app.integrations.gemini`.
_AI_INTEGRATION_MODULE_PREFIXES = (
    "app.integrations.gemini",
    "app.integrations.card_lookup",
    "app.integrations.resend",
    "app.agent.loop",
    "app.agent.memory",
    # Day 25: digest failures land in email_log + ai_call_log; Sentry
    # shouldn't double-log Sonnet 5xx or Resend SDK errors from the
    # cron path. AICallLogError still bypasses rule 2 so the audit
    # canary stays visible.
    "app.services.digest",
    "app.cron.digest",
)

# The one exception class that *bypasses* rule 2. Named by string so we
# don't take a hard import dependency on app.integrations.aicalllog at
# Sentry-init time (the filter is also exercised by unit tests that
# construct event dicts manually).
_AUDIT_PIPELINE_CANARY = "AICallLogError"


def init_sentry() -> None:
    """Initialize Sentry if `SENTRY_DSN` is set; otherwise no-op.

    Called from `app.main.lifespan`. Missing DSN in dev is intentional —
    the SDK becomes a no-op when uninitialized, so importing
    `sentry_sdk` elsewhere remains safe.

    Integrations: FastAPI/Starlette for the request lifecycle,
    `LoggingIntegration` so `logger.error(...)` becomes a captured event
    at `event_level=ERROR` while `INFO` records remain as breadcrumbs.
    `send_default_pii=False` keeps the SDK from attaching request body /
    user IP / cookies by default; our `before_send` is the second line
    of defense (DESIGN.md §14.5).
    """
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("APP_ENV", "production"),
        send_default_pii=False,
        traces_sample_rate=0.0,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=before_send,
    )


def before_send(event: dict[str, Any], hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Apply the three filter rules + correlation_id tagging + PII redaction.

    Returns `None` to drop the event, the enriched event dict to ship.
    Pure function modulo the `correlation_id` ContextVar read: takes
    the event dict + hint, does not depend on the live SDK state, so
    unit tests can call it directly (the ContextVar defaults to `None`
    outside a request and the tag becomes the literal string "none").
    """
    exception_class = _top_exception_class(event)
    if exception_class == "HTTPException":
        status = _http_exception_status(hint)
        if status is None or (400 <= status < 500):
            # Status known and 4xx → drop (expected user input).
            # Status unknown → drop too: the SDK only captures
            # HTTPException via the FastAPI integration's handler, which
            # always populates the status. An HTTPException with no
            # discernible status would be a malformed event, not a real
            # backend failure.
            return None
    if exception_class != _AUDIT_PIPELINE_CANARY:
        if _originates_from_ai_module(event):
            return None
    _attach_correlation_id_tag(event)
    return _redact_event(event)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _top_exception_class(event: dict[str, Any]) -> str | None:
    """Extract the outermost exception class name from a Sentry event.

    Sentry events represent chained exceptions as a list of frames in
    `event["exception"]["values"]`, with the *outermost* exception
    (typically the one bubbling out of the handler) appearing last.
    """
    values = (event.get("exception") or {}).get("values") or []
    if not values:
        return None
    return values[-1].get("type")


def _http_exception_status(hint: dict[str, Any] | None) -> int | None:
    """Read `status_code` off the live `HTTPException` instance.

    Sentry's `hint` carries the live `(type, instance, traceback)`
    tuple in `hint["exc_info"]`. We read `.status_code` off the
    instance — the only reliable source: `event["contexts"]["response"]`
    is populated by some integrations but not the FastAPI one for
    explicitly-raised `HTTPException`s, so trusting that field would
    miss real 5xx raises.

    Returns `None` if we cannot determine the status — caller treats
    unknown as "drop conservatively" (HTTPExceptions reaching Sentry
    with no inspectable status are a malformed-event signal, not a
    bug-class signal).
    """
    if not hint:
        return None
    exc_info = hint.get("exc_info")
    if not exc_info or len(exc_info) < 2:
        return None
    instance = exc_info[1]
    status = getattr(instance, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _originates_from_ai_module(event: dict[str, Any]) -> bool:
    """Return True if ANY frame of ANY exception value is in an AI module.

    Walks every stack frame of every chained exception value — not just
    the crash-site frame. The crash-site (last) frame of a provider
    failure re-raised bare from the agent loop is `anthropic.*` /
    `httpx.*`, never `app.agent.loop`, so a last-frame-only check shipped
    every Anthropic 5xx / network blip to Sentry even though the same
    failure was already written to ai_call_log with success=False
    (invariant 15 "don't double-log"; audit P2-3). If the call path
    passed through a listed module at all, ai_call_log owns the failure.

    Anchors the match with a trailing dot or exact equality so unrelated
    modules sharing a prefix do not slip through.
    """
    values = (event.get("exception") or {}).get("values") or []
    for value in values:
        frames = ((value or {}).get("stacktrace") or {}).get("frames") or []
        for frame in frames:
            module = (frame or {}).get("module") or ""
            for prefix in _AI_INTEGRATION_MODULE_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    return True
    return False


def _attach_correlation_id_tag(event: dict[str, Any]) -> None:
    """Stamp the current request's `correlation_id` onto the event.

    Sentry alerts surface tags prominently; "search by tag" lets a
    triage operator pivot from a Sentry stack trace to the matching
    line in Railway stdout (where the JSON log records carry the same
    id) and to the response header the frontend logged. Without this
    step the correlation is dead — same id, never compared.

    Reads `asgi_correlation_id.correlation_id` (the library's
    ContextVar). Outside a request that contextvar is `None`; we omit
    the tag rather than write `"none"` so missing-context events do
    not pollute Sentry's tag autocomplete.
    """
    cid = correlation_id.get()
    if cid is None:
        return
    tags = event.setdefault("tags", {})
    if isinstance(tags, dict):
        tags["correlation_id"] = cid


def _redact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Redact the user-content surfaces in a Sentry event.

    Only the fields that can legitimately carry user-typed content are
    touched: request body / query string, free-form `extra`, and the
    `data` payload on breadcrumbs. Everything else (timestamps, tags,
    fingerprint) is preserved verbatim.
    """
    request = event.get("request")
    if isinstance(request, dict):
        if "data" in request:
            request["data"] = redact_mapping(request["data"])
        if "query_string" in request:
            request["query_string"] = redact_mapping(request["query_string"])
    if "extra" in event:
        event["extra"] = redact_mapping(event["extra"])
    # `logentry` carries the log message + %-params on LoggingIntegration
    # events. Today it arrives pre-redacted only because the stdout
    # handler's PiiRedactionFilter happens to mutate the record in place
    # before Sentry's patched callHandlers reads it — a propagate=False
    # third-party logger (or moving the filter) would leak (audit P3-22).
    # `message` is the log FORMAT string, not the chat wire field, so run
    # the pattern redactor over it rather than the by-name wholesale
    # replacement (which would erase every log message).
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        if isinstance(logentry.get("message"), str):
            logentry["message"] = redact_string(logentry["message"])
        if "params" in logentry:
            logentry["params"] = redact_mapping(logentry["params"])
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        crumbs = breadcrumbs.get("values") or []
        for crumb in crumbs:
            if isinstance(crumb, dict) and "data" in crumb:
                crumb["data"] = redact_mapping(crumb["data"])
    elif isinstance(breadcrumbs, list):
        for crumb in breadcrumbs:
            if isinstance(crumb, dict) and "data" in crumb:
                crumb["data"] = redact_mapping(crumb["data"])
    return event
