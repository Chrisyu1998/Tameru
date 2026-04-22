from fastapi import Depends, FastAPI

from app.auth import AuthedUser, get_current_user_jwt
from app.routes import transactions as transactions_routes

app = FastAPI(title="Tameru")

app.include_router(transactions_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/me")
def me(user: AuthedUser = Depends(get_current_user_jwt)) -> dict[str, str]:
    return {"user_id": str(user.user_id), "email": user.email}
