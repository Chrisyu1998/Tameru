"""PII redaction filter — pure unit tests (no Supabase, no network).

Covers each redaction pattern in `app/logging_redaction.py` and confirms
that legitimate content passes through untouched. Two surfaces are
exercised:

  * `redact_string` / `redact_mapping` — used by Sentry's `before_send`
    over event payloads.
  * `PiiRedactionFilter` — installed on the root logger; rewrites
    `LogRecord` fields in place before the formatter emits the line.

Defense-in-depth: every emit path that uses `extra={...}` is supposed
to whitelist its fields, but a sloppy f-string would otherwise leak.
These tests pin the redactor as that fallback (DESIGN.md §14.5).
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from app.logging_config import configure_logging
from app.logging_redaction import (
    PiiRedactionFilter,
    redact_mapping,
    redact_string,
)


# ---------------------------------------------------------------------------
# redact_string — pattern-by-pattern coverage.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected_marker, expected_clean",
    [
        # Email
        ("contact me at user@example.com please", "<redacted:email>", "user@example.com"),
        ("nested <hr@tameru.co> tag", "<redacted:email>", "hr@tameru.co"),
        # Phone
        ("call +1 415-555-0123 now", "<redacted:phone>", "415-555-0123"),
        ("(415) 555-0123", "<redacted:phone>", "(415)"),
        # Card numbers (13–19 digits)
        ("number 4111 1111 1111 1111", "<redacted:card_number>", "4111"),
        ("4242424242424242", "<redacted:card_number>", "4242424242424242"),
        # JWT
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxw",
            "<redacted:jwt>",
            "eyJhbGc",
        ),
        # sb_secret_ — Supabase service-role key prefix
        ("key=sb_secret_abcDEF123_xyz here", "<redacted:service_role_key>", "sb_secret_"),
        # Decimal amount
        ("paid $12.34 for coffee", "<redacted:amount>", "$12.34"),
        ("paid 1,234.56 total", "<redacted:amount>", "1,234.56"),
    ],
)
def test_redact_string_replaces_each_pattern(
    raw: str, expected_marker: str, expected_clean: str
) -> None:
    """Each forbidden pattern is rewritten to <redacted:reason>; the raw
    leaked substring is gone from the output."""
    out = redact_string(raw)
    assert expected_marker in out, f"missing redaction marker in {out!r}"
    assert expected_clean not in out, f"raw substring leaked through: {out!r}"


def test_redact_string_passes_innocuous_content_through() -> None:
    """A line with no forbidden patterns is returned verbatim."""
    line = "request started for /chat/messages with method=POST"
    assert redact_string(line) == line


@pytest.mark.parametrize(
    "raw",
    [
        "client 172.16.0.2:44809",                              # IPv4 + port
        "from 2600:1f18:38df:9500:773b:a8b6:f8bd:9386",         # IPv6
        "version v1.2.3",                                       # semver
        "iso 2026-05-22T20:15:02Z",                             # ISO timestamp
        "port 5173",                                            # bare integer
        "uuid 4bf67dad-1234-5678-9abc-def012345678",            # UUID
        "elapsed 200ms",                                        # latency
    ],
)
def test_redact_string_preserves_innocuous_decimals(raw: str) -> None:
    """IP addresses, semver, UUIDs, dates, and port numbers all contain
    decimal-looking digit groups that the original amount regex
    false-positively redacted (an IP `172.16.0.2` was emitted as
    `<redacted:amount>.0.2:port` in the Railway access log). The
    tightened regex requires a `$` prefix or comma-thousand grouping
    as the disambiguating signal; bare decimals pass through."""
    assert redact_string(raw) == raw


def test_redact_string_handles_non_string_inputs() -> None:
    """`None` / ints / floats pass through unchanged — the caller is
    expected to coerce before calling, but the function must not raise."""
    assert redact_string(None) is None  # type: ignore[arg-type]
    assert redact_string("") == ""


# ---------------------------------------------------------------------------
# redact_mapping — recursive JSON-shaped redaction.
# ---------------------------------------------------------------------------


def test_redact_mapping_redacts_by_field_name() -> None:
    """Field names in the by-name allowlist are replaced wholesale —
    even when the value would not match any pattern (e.g. a numeric
    amount serialized as a Python int)."""
    payload = {
        "amount": 1234,
        "merchant": "Blue Bottle",
        "chat_text": "I want sushi",
        "email": "anon@example.com",
        "phone": "415-555-1212",
        "card_number": "4242424242424242",
        "innocent_field": "keep me",
    }
    out = redact_mapping(payload)
    assert out["amount"] == "<redacted:amount>"
    assert out["merchant"] == "<redacted:merchant>"
    assert out["chat_text"] == "<redacted:chat_text>"
    assert out["email"] == "<redacted:email>"
    assert out["phone"] == "<redacted:phone>"
    assert out["card_number"] == "<redacted:card_number>"
    assert out["innocent_field"] == "keep me"


def test_redact_mapping_recurses_into_nested_structures() -> None:
    """Lists and nested dicts are walked; pattern matches inside string
    values are redacted at any depth."""
    payload = {
        "outer": {
            "items": [
                {"note": "user@example.com here"},
                {"note": "no PII here"},
            ],
        },
    }
    out = redact_mapping(payload)
    assert "<redacted:email>" in out["outer"]["items"][0]["note"]
    assert out["outer"]["items"][1]["note"] == "no PII here"


def test_redact_mapping_preserves_null_amount() -> None:
    """A None amount field is left as None — only non-None by-name
    fields are marked redacted. This matters because Sentry payloads
    sometimes carry null defaults the redactor should not synthesize
    content for."""
    payload = {"amount": None, "merchant": None}
    out = redact_mapping(payload)
    assert out["amount"] is None
    assert out["merchant"] is None


# ---------------------------------------------------------------------------
# PiiRedactionFilter — mutates LogRecord in place.
# ---------------------------------------------------------------------------


def test_filter_redacts_msg_string() -> None:
    """A raw `logger.info(f"saved {tx_amount}")`-style leak is
    redacted in the emitted message before the formatter runs."""
    out = _emit(
        logging.getLogger("test.redact.msg"),
        logging.INFO,
        "saved transaction for $99.50 at example",
        amount=None,
        email=None,
    )
    assert "<redacted:amount>" in out
    assert "$99.50" not in out


def test_filter_redacts_extra_fields_by_name() -> None:
    """Whitelist field names land in `record.__dict__`; the filter
    rewrites those by name regardless of value pattern."""
    out = _emit(
        logging.getLogger("test.redact.extra"),
        logging.INFO,
        "transaction confirmed",
        amount="50.00",
        email="leaked@example.com",
    )
    assert "<redacted:amount>" in out
    assert "<redacted:email>" in out
    assert "leaked@example.com" not in out
    assert "50.00" not in out


def test_filter_returns_true_so_record_is_not_dropped() -> None:
    """The filter must redact, not drop. Silent drops hide bugs."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="check %s", args=("user@example.com",), exc_info=None,
    )
    assert PiiRedactionFilter().filter(record) is True


# ---------------------------------------------------------------------------
# End-to-end: configure_logging + PiiRedactionFilter on JSON output.
# ---------------------------------------------------------------------------


def test_configure_logging_emits_json_with_redaction(capsys: pytest.CaptureFixture[str]) -> None:
    """Full stack: configure_logging() installs the JSON formatter +
    filter on root; a logger.info call lands as one JSON line with the
    leaked email rewritten to <redacted:email>."""
    # Reset the module-level guard so configure_logging re-runs cleanly.
    import app.logging_config as cfg
    cfg._CONFIGURED = False
    configure_logging()
    logging.getLogger("test.e2e").info("contact %s", "leaked@example.com")
    out = capsys.readouterr().out.strip()
    record = json.loads(out.splitlines()[-1])
    assert "<redacted:email>" in record["message"]
    assert "leaked@example.com" not in record["message"]
    assert "correlation_id" in record
    assert "user_id" in record


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _emit(logger: logging.Logger, level: int, msg: str, **extra: object) -> str:
    """Capture one formatted record from a logger as a string."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s|%(amount)s|%(email)s"))
    handler.addFilter(PiiRedactionFilter())
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        logger.log(level, msg, extra=extra)
    finally:
        logger.removeHandler(handler)
    return stream.getvalue().strip()
