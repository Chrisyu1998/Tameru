"""Root-logger setup: JSON formatter + correlation-id + PII redaction.

`configure_logging()` is called from `app.main.lifespan` before any other
startup work so every subsequent record passes through one JSON formatter
with one redaction filter. Uvicorn's `uvicorn.access` and `uvicorn.error`
loggers are routed through the same handler — the whole stdout stream is
one schema (DESIGN.md §14.5).

Each emitted record carries `correlation_id` (from the per-request UUID
threaded by `asgi-correlation-id`'s contextvar) and `user_id` (from
`app.context.user_id_var`, set inside `app.auth.get_current_user_jwt`).
Both default to `None` when read outside a request — that is intentional:
boot logs, cron logs, and test harness logs simply lack the fields.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from asgi_correlation_id import correlation_id
from pythonjsonlogger.json import JsonFormatter

from app.context import user_id_var
from app.logging_redaction import PiiRedactionFilter

_CONFIGURED = False

_STANDARD_FIELDS = (
    "timestamp",
    "level",
    "logger",
    "message",
    "correlation_id",
    "user_id",
)

# Loggers we route through our handler. The empty string is the root
# logger; uvicorn's are explicitly listed so its access lines are JSON,
# not the default `INFO:     127.0.0.1:... "GET / HTTP/1.1" 200`.
_MANAGED_LOGGERS = ("", "uvicorn", "uvicorn.access", "uvicorn.error")


def configure_logging() -> None:
    """Install the JSON formatter + redaction filter on the root logger.

    Idempotent — calling twice (e.g. from a re-entered lifespan in a
    test harness) is safe. The handler is attached only to the root
    logger; per-logger handlers are removed so a child like
    `uvicorn.access` does not double-emit.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = _resolve_level()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter())
    handler.addFilter(PiiRedactionFilter())
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Strip child handlers so log records propagate to the root handler
    # once — uvicorn installs its own otherwise and we'd double-emit.
    for name in _MANAGED_LOGGERS:
        if name == "":
            continue
        child = logging.getLogger(name)
        for existing in list(child.handlers):
            child.removeHandler(existing)
        child.setLevel(level)
        child.propagate = True

    _CONFIGURED = True


def context_extra(**fields: Any) -> dict[str, Any]:
    """Return a dict suitable for `logger.info(..., extra=context_extra(...))`.

    Convenience wrapper: it adds nothing structural beyond the input
    dict, but funnels every caller through the same helper so future
    changes (e.g. forcing all keys through `redact_mapping` at call
    time) have one place to land.
    """
    return dict(fields)


class _ContextFilter(logging.Filter):
    """Attach `correlation_id` and `user_id` to every record.

    Reads both contextvars at filter time (i.e. on the request thread).
    Out-of-request logs (boot, cron, tests) get `None` for both — which
    serializes as JSON `null`, the desired wire shape per §14.5.

    A class (not a function) because `logging.Filter` is the documented
    extension point; underscore-prefixed because no external module
    instantiates it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Stamp the record with the request-scoped identifiers."""
        record.correlation_id = correlation_id.get()
        record.user_id = user_id_var.get()
        return True


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _resolve_level() -> int:
    """Resolve the effective log level (DESIGN.md §14.5 convention).

    Honor `LOG_LEVEL` first; otherwise default to `DEBUG` in dev and
    `INFO` elsewhere. Unknown level strings fall back to `INFO`.
    """
    explicit = os.environ.get("LOG_LEVEL", "").upper()
    if explicit in logging.getLevelNamesMapping():
        return logging.getLevelNamesMapping()[explicit]
    if os.environ.get("APP_ENV", "").lower() == "dev":
        return logging.DEBUG
    return logging.INFO


def _build_formatter() -> JsonFormatter:
    """Build the JSON formatter emitting the §14.5 schema.

    `rename_fields` maps stdlib attribute names to the wire-shape names
    documented in DESIGN.md §14.5 (`asctime` → `timestamp`, `levelname`
    → `level`, `name` → `logger`).
    """
    return JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(correlation_id)s %(user_id)s",
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        json_indent=None,
    )
