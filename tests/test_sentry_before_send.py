"""Sentry before_send filter — pure unit tests.

Constructs Sentry-shaped event dicts directly so the SDK does not need
to be initialized. The filter is a pure function over the event dict;
its three rules (DESIGN.md §14.5) are exercised:

  1. Drop HTTPException only when its status is 4xx — 5xx is an
     explicit server-error raise and must reach Sentry.
  2. Drop events whose origin module is an AI integration —
     `ai_call_log` already records those failures.
  3. *Exception* to rule 2: AICallLogError ships unconditionally,
     because that class signals the audit pipeline itself failed.

After filtering, the surviving event is tagged with the request
`correlation_id` (from `asgi_correlation_id`'s ContextVar) and run
through `redact_mapping` so request bodies, query strings, `extra`,
and breadcrumb data carry no PII.
"""

from __future__ import annotations

from typing import Any

from asgi_correlation_id import correlation_id
from fastapi import HTTPException

from app.sentry_filters import before_send


def test_rule1_drops_4xx_http_exception() -> None:
    """A 4xx HTTPException is user input, not a backend bug — dropped."""
    event = _event(exc_type="HTTPException")
    hint = _http_exception_hint(status_code=422)
    assert before_send(event, hint) is None


def test_rule1_keeps_5xx_http_exception() -> None:
    """A 5xx HTTPException is an explicit server-error raise — kept.

    Codex-flagged P2: there are real `raise HTTPException(500, ...)`
    sites (e.g. `app/routes/cards.py`) that the old "drop all
    HTTPException" rule was silently swallowing. 5xx is exactly the
    bug-class signal Sentry exists to catch.
    """
    event = _event(exc_type="HTTPException")
    hint = _http_exception_hint(status_code=503)
    out = before_send(event, hint)
    assert out is not None, "5xx HTTPException must ship to Sentry"


def test_rule1_drops_http_exception_with_unknown_status() -> None:
    """An HTTPException reaching Sentry with no inspectable status is
    treated conservatively — drop. The FastAPI integration always
    populates `status_code` on the live instance, so an unknown status
    here would be a malformed event, not a real backend signal.
    """
    event = _event(exc_type="HTTPException")
    assert before_send(event, None) is None  # no hint at all
    assert before_send(event, {"exc_info": (None, None, None)}) is None


def test_rule2_drops_ai_integration_module_exceptions() -> None:
    """A RuntimeError raised inside app.integrations.gemini is dropped —
    ai_call_log is the source of truth for AI failures (§14.2)."""
    for module in (
        "app.integrations.gemini",
        "app.integrations.card_lookup",
        "app.agent.loop",
        "app.agent.memory",
    ):
        event = _event(exc_type="RuntimeError", module=module)
        assert before_send(event, None) is None, f"should have dropped from {module}"


def test_rule2_drops_submodule_under_ai_integration() -> None:
    """Prefix match anchors with a trailing dot — submodules of an AI
    integration module are also dropped."""
    event = _event(exc_type="RuntimeError", module="app.integrations.gemini.client")
    assert before_send(event, None) is None


def test_rule2_does_not_drop_unrelated_app_modules() -> None:
    """A RuntimeError from a route handler ships normally — the rule 2
    prefix list does not bleed into unrelated app modules."""
    event = _event(exc_type="RuntimeError", module="app.routes.transactions")
    assert before_send(event, None) is not None


def test_rule3_aicalllog_error_ships_even_from_ai_integration_module() -> None:
    """AICallLogError is the canary for the audit pipeline itself
    failing — the rule-2 drop MUST be skipped for this class."""
    event = _event(exc_type="AICallLogError", module="app.integrations.gemini")
    out = before_send(event, None)
    assert out is not None, "AICallLogError must not be dropped — it is the audit canary"


def test_event_without_exception_passes_through() -> None:
    """An event without an exception (e.g. an explicit
    `sentry_sdk.capture_message`) ships through redaction; rule 1/2
    extraction returns None and the filter does not drop it."""
    event = {"message": "explicit capture", "extra": {}}
    out = before_send(event, None)
    assert out is not None


def test_redacts_request_body_on_survivor() -> None:
    """A surviving event has its request body run through redaction —
    raw amounts and emails do not reach Sentry."""
    event = _event(
        exc_type="RuntimeError",
        request={
            "data": {
                "amount": 99.50,
                "email": "leaked@example.com",
                "harmless": "stays",
            },
        },
    )
    out = before_send(event, None)
    assert out is not None
    assert out["request"]["data"]["amount"] == "<redacted:amount>"
    assert out["request"]["data"]["email"] == "<redacted:email>"
    assert out["request"]["data"]["harmless"] == "stays"


def test_redacts_query_string_on_survivor() -> None:
    """Query strings can carry user input; the redactor scans for
    embedded amounts/emails just like the request body."""
    event = _event(
        exc_type="RuntimeError",
        request={"query_string": "filter=user@example.com&page=2"},
    )
    out = before_send(event, None)
    assert "<redacted:email>" in out["request"]["query_string"]
    assert "user@example.com" not in out["request"]["query_string"]


def test_redacts_extra_payload_on_survivor() -> None:
    """`event.extra` carries any user-attached debugging context — the
    redactor must reach it."""
    event = _event(
        exc_type="RuntimeError",
        extra={"merchant": "Blue Bottle", "method": "POST"},
    )
    out = before_send(event, None)
    assert out["extra"]["merchant"] == "<redacted:merchant>"
    assert out["extra"]["method"] == "POST"


def test_redacts_breadcrumb_data() -> None:
    """Breadcrumbs (`event.breadcrumbs.values[*].data`) are walked
    too — sentry-sdk's automatic HTTP-request breadcrumbs can otherwise
    leak query strings or response bodies."""
    event = _event(
        exc_type="RuntimeError",
        breadcrumbs={
            "values": [
                {"data": {"email": "trace@example.com", "ok": True}},
            ],
        },
    )
    out = before_send(event, None)
    crumb = out["breadcrumbs"]["values"][0]
    assert crumb["data"]["email"] == "<redacted:email>"
    assert crumb["data"]["ok"] is True


def test_survivor_carries_correlation_id_tag() -> None:
    """A surviving event is tagged with the request's `correlation_id`
    so a Sentry alert ties back to the matching Railway log line and
    the `X-Request-ID` response header. Codex-flagged P3.
    """
    token = correlation_id.set("e45c99ec8bc64720b6ad28dac94cf4a9")
    try:
        event = _event(exc_type="RuntimeError")
        out = before_send(event, None)
        assert out is not None
        assert out["tags"]["correlation_id"] == "e45c99ec8bc64720b6ad28dac94cf4a9"
    finally:
        correlation_id.reset(token)


def test_survivor_omits_correlation_id_tag_when_outside_request() -> None:
    """Outside a request (e.g. a background-thread `capture_exception`
    or test runtime) `correlation_id.get()` is None. We omit the tag
    rather than write `"none"` so Sentry's tag autocomplete is not
    polluted with a useless value."""
    # ContextVar default is None — no `correlation_id.set(...)` here.
    event = _event(exc_type="RuntimeError")
    out = before_send(event, None)
    assert out is not None
    assert "correlation_id" not in out.get("tags", {})


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _event(
    *,
    exc_type: str,
    module: str = "app.routes.transactions",
    **extras: Any,
) -> dict[str, Any]:
    """Build a minimal Sentry event dict for the filter to chew on."""
    base: dict[str, Any] = {
        "exception": {
            "values": [
                {
                    "type": exc_type,
                    "stacktrace": {
                        "frames": [
                            {"module": module, "function": "handler"},
                        ],
                    },
                },
            ],
        },
    }
    base.update(extras)
    return base


def _http_exception_hint(*, status_code: int) -> dict[str, Any]:
    """Build a Sentry `hint` carrying a live `HTTPException` instance.

    Mirrors the shape Sentry's FastAPI integration passes to
    `before_send`: `hint["exc_info"] = (type, instance, traceback)`.
    `before_send` reads `.status_code` off `instance` to distinguish
    4xx from 5xx.
    """
    exc = HTTPException(status_code=status_code, detail="test")
    return {"exc_info": (type(exc), exc, None)}
