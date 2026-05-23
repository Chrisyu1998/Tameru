import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from app.routes import subscriptions as subscriptions_routes
from app.routes import transactions as transactions_routes
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
def me(user: AuthedUser = Depends(get_current_user_jwt)) -> dict[str, str | None]:
    """Returns the verified JWT identity plus the user's home currency.

    `home_currency` is null when no `users_meta` row exists yet (new user
    who hasn't completed onboarding's currency picker). The frontend keys
    its dispatch off this — null routes to ConfirmHomeCurrency, non-null
    routes through claim_device into the app. Stays outside the device
    gate (uses `get_current_user_jwt`, not `get_current_user_with_device`)
    because the frontend has to read this *before* it knows whether to
    bootstrap or claim — see app/auth.py.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("users_meta")
        .select("home_currency")
        .eq("user_id", str(user.user_id))
        .execute()
    )
    home_currency = resp.data[0]["home_currency"] if resp.data else None
    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "home_currency": home_currency,
    }


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

# The read-only MCP server (app/mcp_server.py) — a self-contained ASGI app
# with its own Streamable HTTP transport. Mounted at /mcp; its session
# manager is started in `lifespan` above. The OAuth protected-resource
# metadata route is additionally registered at the app root: the SDK
# advertises it at /.well-known/oauth-protected-resource/mcp (RFC 9728),
# and the /mcp mount alone would bury it one level deeper where discovery
# clients cannot reach it.
app.mount("/mcp", mcp_app)
app.router.routes.extend(mcp_well_known_routes)
