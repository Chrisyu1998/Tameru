import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware, correlation_id
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.auth import AuthedUser, get_current_user_jwt
from app.db import supabase_for_user
from app.logging_config import configure_logging
from app.mcp_server import mcp_app, mcp_server, mcp_well_known_routes
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import cards as cards_routes
from app.routes import chat as chat_routes
from app.routes import dashboard as dashboard_routes
from app.routes import goals as goals_routes
from app.routes import imports as imports_routes
from app.routes import memory as memory_routes
from app.routes import preferences as preferences_routes
from app.routes import subscriptions as subscriptions_routes
from app.routes import transactions as transactions_routes
from app.routes import export as export_routes
from app.routes import unsubscribe as unsubscribe_routes
from app.routes import webhooks_resend as webhooks_resend_routes
from app.sentry_filters import init_sentry

# Environment variables every authenticated request path depends on. A
# process can boot without them and still answer /healthz, then 500 on the
# first request that needs one — `lifespan` turns that into a loud boot
# failure instead. The Supabase service-role key is deliberately absent:
# it belongs to pg_cron and migrations, never to a request handler
# (CLAUDE.md invariant 1), so a request-serving process does not require it.
_REQUIRED_ENV_VARS = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "IMPORT_TOKEN_SECRET",
    # Public URL of the mounted MCP server (app/mcp_server.py). Missing,
    # it would not 500 a request — it would silently mis-advertise the
    # OAuth protected-resource metadata, so fail fast at boot instead.
    "MCP_RESOURCE_SERVER_URL",
    # Day 25 (DESIGN.md §6.4). The one-click unsubscribe route and the
    # Resend bounce webhook both run inside the request-serving process;
    # without these secrets they 500 on the first hit. RESEND_API_KEY is
    # deliberately NOT required here — it's only used by the cron at
    # `python -m app.cron.digest`, which loads its own env.
    "DIGEST_UNSUBSCRIBE_SECRET",
    "RESEND_WEBHOOK_SECRET",
    # The unsubscribe and Resend-webhook routes call supabase_admin()
    # (per-file allowlist on the service-role leak test — CLAUDE.md
    # invariant 1 admits both as sanctioned callers since their inbound
    # requests carry no user JWT). Without this, every valid unsubscribe
    # click and every bounce webhook 500s after Svix/HMAC verification —
    # a deployment satisfying every other invariant would still serve
    # broken opt-outs. Codex 2026-05-23.
    "SUPABASE_SERVICE_ROLE_KEY",
    # Public URL of this backend (e.g. https://tameru-production.up.railway.app).
    # The digest cron embeds it in every email's List-Unsubscribe URL,
    # and the request-serving process needs it for any future feature
    # that links back to itself in an outbound message. Distinct from
    # FRONTEND_ORIGIN (the Vercel PWA host) because /unsubscribe is a
    # FastAPI route, not an SPA route — the Vercel catch-all would
    # otherwise serve index.html and Gmail one-click POSTs would never
    # land on the suppression handler.
    "BACKEND_PUBLIC_URL",
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Refuse to start the server when required configuration is missing.

    Without this check, a deploy that forgot an env var boots a
    healthy-looking process (/healthz still returns 200) that 500s on the
    first request touching that var. Because that 500 is synthesized
    outside `CORSMiddleware` (see `_unhandled_exception_handler`), the
    browser blocks it and the frontend shows an opaque "Load failed" with
    no diagnostic — exactly the failure mode that hid a missing
    `IMPORT_TOKEN_SECRET` from the CSV-import route. Validating at boot
    turns a silent per-request failure into an immediate, logged crash the
    deploy surfaces.

    Runs under `uvicorn app.main:app`. A bare `TestClient(app)` does not
    enter the lifespan (Starlette only runs it inside the client's
    context-manager form), so the test suite is unaffected.
    """
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    # GEMINI_MODEL / GEMINI_MODEL_DEFAULT are an at-least-one pair
    # (app/integrations/gemini.py::_model_name). Report the prod-facing
    # name when neither is set.
    if not (os.environ.get("GEMINI_MODEL") or os.environ.get("GEMINI_MODEL_DEFAULT")):
        missing.append("GEMINI_MODEL_DEFAULT")
    # SENTRY_DSN is required in production; absent in dev means the SDK
    # is a no-op (DESIGN.md §14.5). Missing prod DSN = no error capture,
    # which is worse than a noisy boot crash that demands a fix.
    if os.environ.get("APP_ENV", "").lower() == "production" and not os.environ.get("SENTRY_DSN"):
        missing.append("SENTRY_DSN")
    # FRONTEND_ORIGIN is the Vercel PWA host added to the CORS allowlist
    # (`_cors_allowed_origins()`). Unconditionally required would break
    # local boots — `.env.example` documents it as commented-out for dev
    # because http://localhost:5173 is always allowed. In production a
    # missing value means every browser request 4xxs on the CORS preflight,
    # so fail loud there. The digest cron's own module-level check
    # (`app/cron/digest.py::_app_cta_url`) covers the cron service
    # regardless of APP_ENV.
    if os.environ.get("APP_ENV", "").lower() == "production" and not os.environ.get("FRONTEND_ORIGIN"):
        missing.append("FRONTEND_ORIGIN")
    if missing:
        raise RuntimeError(
            "Tameru refusing to start — missing required environment "
            "variable(s): " + ", ".join(sorted(missing)) + ". Set them in "
            "Railway's env UI (see .env.example)."
        )
    # Observability foundation (DESIGN.md §14.5). Must run before any
    # other startup work so subsequent boot logs already pass through
    # the JSON formatter + redaction filter; Sentry init is idempotent
    # and no-ops when SENTRY_DSN is unset (dev).
    configure_logging()
    init_sentry()
    # The read-only MCP server (app/mcp_server.py) is mounted at /mcp; its
    # Streamable HTTP transport needs the session manager running for the
    # lifetime of the process.
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="Tameru", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    """Provide healthz."""
    return {"ok": True}


@app.get("/me")
def me(
    user: AuthedUser = Depends(get_current_user_jwt),
) -> dict[str, str | bool | None]:
    """Returns the verified JWT identity plus the user's preference columns.

    `home_currency` is null when no `users_meta` row exists yet (new user
    who hasn't completed onboarding's currency picker). The frontend keys
    its dispatch off this — null routes to ConfirmHomeCurrency, non-null
    routes through claim_device into the app. Stays outside the device
    gate (uses `get_current_user_jwt`, not `get_current_user_with_device`)
    because the frontend has to read this *before* it knows whether to
    bootstrap or claim — see app/auth.py.

    `analytics_opted_out` and `weekly_digest_enabled` ride along so the
    Day 26 PostHog wrapper can initialize opted-out by default and only
    flip to opted-in once this response confirms — no events leak between
    SDK boot and the first user action. Pre-bootstrap users (no
    `users_meta` row) default to the column defaults: opted in to
    analytics (false), opted in to the digest (true).
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("users_meta")
        .select(
            "home_currency, analytics_opted_out, weekly_digest_enabled, "
            "timezone, ui_language"
        )
        .eq("user_id", str(user.user_id))
        .execute()
    )
    row = resp.data[0] if resp.data else {}
    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "home_currency": row.get("home_currency"),
        "analytics_opted_out": bool(row.get("analytics_opted_out", False)),
        "weekly_digest_enabled": bool(row.get("weekly_digest_enabled", True)),
        "timezone": row.get("timezone"),
        # Day 29 Tier 2 (DESIGN.md §6.6): the UI/display language, or null
        # when unset (frontend falls back to navigator.language). Rides on
        # /me so first paint resolves the formatting locale in one round trip.
        "ui_language": row.get("ui_language"),
    }


# Minimal hardening for the JSON API (Day 27). The full CSP lives at the
# Vercel edge — CSP protects the document origin, and this process serves
# only JSON to a cross-origin Bearer-authenticated client (no HTML, no
# scripts). The three headers below still matter even without a document:
#   - X-Content-Type-Options: nosniff blocks MIME-sniffing on JSON
#     responses, neutralizing the rare "API returns text the browser
#     decides to execute as a script" trick.
#   - X-Frame-Options: DENY ensures error pages or any HTML accidentally
#     served from this origin can't be framed (modern browsers prefer CSP
#     frame-ancestors, set on the Vercel side; this is the legacy-browser
#     fallback for the API origin).
#   - Referrer-Policy: no-referrer keeps the API origin out of outbound
#     Referer headers if the user ever clicks a link rendered from a JSON
#     response (e.g. a 404 page from a typo'd URL).
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardening headers to every JSON-API response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Run the request and stamp hardening headers on the response."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _cors_allowed_origins() -> list[str]:
    """Explicit cross-origin allowlist for dev and production frontends.

    Local dev always allows the Vite server. Production adds
    `FRONTEND_ORIGIN`, with no wildcard or `*.vercel.app` catch-all.
    """
    origins = ["http://localhost:5173"]
    prod_origin = os.environ.get("FRONTEND_ORIGIN")
    if prod_origin:
        origins.append(prod_origin)
    return origins


# Resolved once at import — shared by `CORSMiddleware` and the
# unhandled-exception handler so a 500 echoes the exact same allowlist.
_CORS_ALLOWED_ORIGINS = _cors_allowed_origins()


app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "X-Device-Id", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    # Bearer tokens in the Authorization header — never cookies. Keeping
    # credentials off sidesteps SameSite / third-party-cookie complexity.
    allow_credentials=False,
)

# Added BEFORE CorrelationIdMiddleware in source so it sits OUTSIDE Cors
# in the runtime stack — the headers therefore ride on preflight 200s.
# NOT on the synthesized 500 from `_unhandled_exception_handler`: that
# handler runs inside Starlette's ServerErrorMiddleware, which is
# outermost — outside every user middleware — so the handler stamps the
# hardening headers and X-Request-ID itself (audit P3-24).
app.add_middleware(SecurityHeadersMiddleware)

# CorrelationIdMiddleware mounts AFTER CORSMiddleware in the source but
# runs as the OUTERMOST middleware at request time — Starlette's
# middleware stack is built LIFO. Honors `X-Request-ID` from Railway's
# edge if present; mints a fresh UUIDv4 otherwise; echoes the id back in
# the response header so the frontend can correlate failures with stdout
# / Sentry. DESIGN.md §14.5.
app.add_middleware(CorrelationIdMiddleware)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Return a CORS-visible 500 for any exception no route handler caught.

    Starlette runs `CORSMiddleware` *inside* its outermost
    `ServerErrorMiddleware`, so a 500 synthesized from an unhandled
    exception ships with no `Access-Control-Allow-Origin` header. A
    cross-origin browser then blocks the response and the frontend sees an
    opaque network failure ("Load failed") instead of the real error.
    Re-attaching the allow-origin header here keeps unhandled-exception
    responses legible cross-origin so the UI can render a real message and
    code. `ServerErrorMiddleware` still re-raises `exc` after this response
    is sent, so uvicorn and Sentry log the traceback unchanged.

    `HTTPException` is unaffected — Starlette resolves it via the inner
    `ExceptionMiddleware`, whose responses already pass back out through
    `CORSMiddleware`. This handler only catches genuinely unhandled
    exceptions. The body matches the API-wide `{detail: {code, message}}`
    shape so the frontend's `ApiError` parsing surfaces `internal_error`
    rather than a bare HTTP status.
    """
    response = JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": "the server hit an unexpected error.",
            }
        },
    )
    origin = request.headers.get("origin")
    if origin and origin in _CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    # ServerErrorMiddleware (where this handler runs) is OUTERMOST, so
    # this response never traverses SecurityHeadersMiddleware or
    # CorrelationIdMiddleware — stamp the hardening headers and the
    # request id here too, or the 500 ships bare and the frontend loses
    # its X-Request-ID correlation handle (audit P3-24).
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    cid = correlation_id.get()
    if cid:
        response.headers["X-Request-ID"] = cid
    return response


app.include_router(auth_routes.router)
app.include_router(transactions_routes.router)
app.include_router(chat_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(cards_routes.router)
app.include_router(memory_routes.router)
app.include_router(goals_routes.router)
app.include_router(subscriptions_routes.router)
app.include_router(imports_routes.router)
app.include_router(admin_routes.router)
app.include_router(preferences_routes.router)
app.include_router(export_routes.router)
app.include_router(unsubscribe_routes.router)
app.include_router(webhooks_resend_routes.router)

# The read-only MCP server (app/mcp_server.py) — a self-contained ASGI app
# with its own Streamable HTTP transport. Mounted at /mcp; its session
# manager is started in `lifespan` above. The OAuth protected-resource
# metadata route is additionally registered at the app root: the SDK
# advertises it at /.well-known/oauth-protected-resource/mcp (RFC 9728),
# and the /mcp mount alone would bury it one level deeper where discovery
# clients cannot reach it.
app.mount("/mcp", mcp_app)
app.router.routes.extend(mcp_well_known_routes)
