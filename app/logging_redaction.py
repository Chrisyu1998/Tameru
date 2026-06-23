"""PII redaction for application logs and Sentry events (DESIGN.md §14.5).

The privacy posture (CLAUDE.md "Privacy posture" + invariant 15) bans
transaction amounts, merchant text, chat content, emails, phones, full
card numbers, JWTs, and the Supabase service-role key from every
observability surface. A `logging.Filter` runs over each `LogRecord`
*before* the JSON formatter emits the line; Sentry's `before_send` runs
the same redactor over the event payload.

Redaction is defense in depth — every emit path that uses `extra={...}`
is supposed to whitelist its fields, but a sloppy `logger.info(f"saved
{tx}")` would otherwise leak. We rewrite forbidden values to
`<redacted:reason>` rather than silently dropping the record: silent
drops hide bugs, redacted strings still tell us what the failure mode
was without leaking the value.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# The set of attribute names a LogRecord acquires from the stdlib
# `logging` module itself. `record.__dict__` includes every kwarg passed
# via `extra={...}` plus these baseline keys; we redact the former and
# leave the latter intact (the formatter consumes them).
_LOGRECORD_BASELINE_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})

# Field-name allowlist: a redacted-by-name field is replaced regardless
# of its value. The list mirrors DESIGN.md §14.5's "redaction set".
# "message" is the /chat/turn wire field carrying the user's chat text
# (audit P2-4) — without it, a Sentry event with the chat request body
# attached shipped the prose verbatim (the value patterns below only
# catch emails/amounts/etc., not free text). Harmless for LogRecords:
# `message` is a `_LOGRECORD_BASELINE_KEYS` member, which the filter
# skips before this map is consulted.
_REDACT_BY_NAME = {
    "amount": "amount",
    "merchant": "merchant",
    "chat_text": "chat_text",
    "message_text": "chat_text",
    "message": "chat_text",
    "email": "email",
    "phone": "phone",
    "card_number": "card_number",
}

# Value-pattern regexes: scanned over every non-baseline string value
# (both the formatted `message` and any `extra` strings). Each tuple is
# `(reason, compiled_regex)`. Order matters — JWT and service-role-key
# detection runs before the generic decimal pattern so a JWT containing
# digits is not partially shadowed.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)
_CARD_NUMBER_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
# Supabase service-role keys come in two shapes: the newer `sb_secret_*`
# format and the legacy `eyJ...` PostgREST JWT (already caught by
# `_JWT_RE`). Catching `sb_secret_` here closes the gap for the new
# format. We deliberately do NOT match by the env var name itself — the
# leak-guard contract test (tests/contracts/test_no_service_role_leak.py)
# scans source for that literal token (memory.md 2026-05-20).
_SERVICE_ROLE_RE = re.compile(r"sb_secret_[A-Za-z0-9_-]+")
# Amount pattern: requires either a `$` prefix OR comma-thousand
# grouping (e.g. `1,234.56`) as the disambiguating signal. An earlier
# version matched bare `25.99`-style decimals, which false-positively
# ate IP address octet pairs (`172.16` in `172.16.0.2:44809` got
# rewritten to `<redacted:amount>.0.2:44809` in the Railway access
# log). The same regex also false-matched semver (`1.2.3` octet pairs)
# and date fragments. Bare decimal-without-currency-prefix is too
# ambiguous to redact safely; transaction amounts in app logs almost
# always carry a `$` prefix or land in an `extra={"amount": ...}` slot
# (caught by the by-name redactor regardless of value).
_AMOUNT_RE = re.compile(
    # Currency-prefixed: $25, $25.99, $1234, $1,234.56
    r"(?<![A-Za-z0-9._])\$-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![A-Za-z0-9])"
    # No prefix but comma-thousand grouping is the signal: 1,234.56
    r"|(?<![A-Za-z0-9._])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![A-Za-z0-9])"
)

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("service_role_key", _SERVICE_ROLE_RE),
    ("jwt", _JWT_RE),
    ("email", _EMAIL_RE),
    ("phone", _PHONE_RE),
    ("card_number", _CARD_NUMBER_RE),
    ("amount", _AMOUNT_RE),
)


def redact_string(value: str) -> str:
    """Redact every forbidden pattern in `value`.

    Each match is replaced with `<redacted:reason>` where `reason` names
    the pattern that fired. The function never raises; non-`str` inputs
    are passed through (the caller is expected to coerce).
    """
    if not isinstance(value, str) or not value:
        return value
    for reason, pattern in _VALUE_PATTERNS:
        value = pattern.sub(f"<redacted:{reason}>", value)
    return value


def redact_mapping(payload: Any) -> Any:
    """Recursively redact a JSON-shaped payload (dict/list/scalar).

    Used by Sentry's `before_send` over `event.request.data`,
    `event.extra`, etc. Mirrors the LogRecord filter: by-name keys are
    replaced wholesale; string values are run through `redact_string`.
    Non-string, non-container scalars are passed through.
    """
    if isinstance(payload, dict):
        return {
            key: (
                f"<redacted:{_REDACT_BY_NAME[key]}>"
                if key in _REDACT_BY_NAME and payload[key] is not None
                else redact_mapping(payload[key])
            )
            for key in payload
        }
    if isinstance(payload, list):
        return [redact_mapping(item) for item in payload]
    if isinstance(payload, str):
        return redact_string(payload)
    return payload


class PiiRedactionFilter(logging.Filter):
    """Rewrite `LogRecord` fields before emit.

    Filter, not handler — installed on the root logger so every record
    is sanitized regardless of which handler ultimately emits it. The
    filter mutates `record.msg`, `record.args`, and any non-baseline
    attribute (i.e. `extra={...}` kwargs); baseline LogRecord keys are
    left alone so the formatter can still read them.

    Returns `True` (the record proceeds) in every case: the goal is to
    redact, not to drop. Silent drops hide bugs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact a single record in place."""
        if isinstance(record.msg, str):
            record.msg = redact_string(record.msg)
        if record.args:
            record.args = self._redact_args(record.args)
        for key, value in list(record.__dict__.items()):
            if key in _LOGRECORD_BASELINE_KEYS:
                continue
            if key in _REDACT_BY_NAME and value is not None:
                record.__dict__[key] = f"<redacted:{_REDACT_BY_NAME[key]}>"
            else:
                record.__dict__[key] = redact_mapping(value)
        return True

    @staticmethod
    def _redact_args(args: object) -> object:
        """Redact tuple/dict args passed positionally to `logger.X(msg, *args)`.

        `logger.info("user=%s", user_email)` lands `user_email` in
        `record.args`; the formatter applies `%`-formatting *after* our
        filter runs, so redacting here catches the leak before emit.

        Arbitrary objects (exceptions, pydantic errors) are coerced to a
        redacted string here rather than passed through: `%s`-formatting
        would stringify them AFTER the filter ran, so an object whose
        `str()` embeds PII (e.g. a ValidationError's `input_value=`)
        defeated a pass-through (audit P3-21). Coercing changes nothing
        for `%s` (which calls `str()` anyway) — primitives keep their
        type so `%d`/`%f` formatting still works.
        """
        if isinstance(args, dict):
            return {key: PiiRedactionFilter._coerce_arg(val) for key, val in args.items()}
        if isinstance(args, tuple):
            return tuple(PiiRedactionFilter._coerce_arg(val) for val in args)
        return PiiRedactionFilter._coerce_arg(args)

    @staticmethod
    def _coerce_arg(value: object) -> object:
        """Redact one %-format arg, stringifying non-JSON-shaped objects.

        Containers and strings go through `redact_mapping`; numeric /
        bool / None primitives pass through (so `%d` formatting keeps
        working); anything else is stringified and pattern-redacted —
        the formatter would have stringified it after the filter anyway,
        bypassing redaction.
        """
        if isinstance(value, tuple):
            return tuple(PiiRedactionFilter._coerce_arg(v) for v in value)
        if isinstance(value, (dict, list, str)):
            return redact_mapping(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return redact_string(str(value))
