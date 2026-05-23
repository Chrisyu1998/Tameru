"""Request-scoped contextvars for cross-cutting observability.

`user_id_var` carries the verified user id from JWT verification into log
records and Sentry events without threading it through every function
signature. Set inside `app.auth.get_current_user_jwt` after a successful
`verify_supabase_jwt`; cleared by the per-request middleware in
`app.main` on response so a background task does not inherit a stale
value (DESIGN.md §14.5).

The JSON formatter and `sentry_sdk.set_user` both read this contextvar;
a single source of truth means stdout, Sentry, and the response header
all describe the same identity.
"""

from __future__ import annotations

from contextvars import ContextVar

user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
