"""Request-scoped contextvars for cross-cutting observability.

`user_id_var` carries the verified user id from JWT verification into log
records and Sentry events without threading it through every function
signature. Set inside `app.auth.get_current_user_jwt` after a successful
`verify_supabase_jwt` (the MCP path mirrors this in
`app.mcp_server._current_user`). There is deliberately NO clearing
middleware: Python's per-task ContextVar scoping discards the value when
the request task ends, and a FastAPI BackgroundTask scheduled by a route
*inherits* the request's context by design — so e.g. the piggyback
distillation task logs under the user that triggered it (see
app/auth.py for the full rationale; DESIGN.md §14.5).

The JSON formatter and `sentry_sdk.set_user` both read this contextvar;
a single source of truth means stdout, Sentry, and the response header
all describe the same identity.
"""

from __future__ import annotations

from contextvars import ContextVar

user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
