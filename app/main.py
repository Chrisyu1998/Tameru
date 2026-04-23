import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import AuthedUser, get_current_user_jwt
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

app.include_router(transactions_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/me")
def me(user: AuthedUser = Depends(get_current_user_jwt)) -> dict[str, str]:
    return {"user_id": str(user.user_id), "email": user.email}
