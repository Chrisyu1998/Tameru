import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import AuthedUser, get_current_user_jwt
from app.db import supabase_for_user
from app.routes import auth as auth_routes
from app.routes import transactions as transactions_routes

app = FastAPI(title="Tameru")


def _cors_allowed_origins() -> list[str]:
    """Explicit cross-origin allowlist for the Vite dev server and the
    production Vercel frontend (DESIGN.md §5.3, §9.3).

    - Local dev always allows http://localhost:5173.
    - Production adds whatever FRONTEND_ORIGIN is set to in Railway
      (e.g. https://tameru.app).

    No wildcards, no *.vercel.app catch-all — any Vercel tenant could
    otherwise reach the API. Preview-deploy URLs hit a staging backend
    when/if staging exists; v1 does not ship one.
    """
    origins = ["http://localhost:5173"]
    prod_origin = os.environ.get("FRONTEND_ORIGIN")
    if prod_origin:
        origins.append(prod_origin)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "X-Device-Id", "Content-Type"],
    # Bearer tokens in the Authorization header — never cookies. Keeping
    # credentials off sidesteps SameSite / third-party-cookie complexity.
    allow_credentials=False,
)

app.include_router(auth_routes.router)
app.include_router(transactions_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
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
