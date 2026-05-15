"""End-to-end smoke test against the production deployment.

Signs in as a dedicated smoke-test user (password auth, not OTP), claims
a synthetic device, then exercises the transaction write/read/delete loop
through the Railway-hosted FastAPI. Reports per-step timing and exits
non-zero on the first failure.

Run after every prod deploy:

    source .venv/bin/activate
    python scripts/smoke_prod.py

Requires four env vars (export them or put them in `.env`):

    SMOKE_API_URL          # e.g. https://tameru-production.up.railway.app
    SMOKE_SUPABASE_URL     # e.g. https://bvehjjrtcnnhjmsyoheb.supabase.co
    SMOKE_SUPABASE_ANON_KEY  # prod anon JWT
    SMOKE_TEST_EMAIL       # deliverable email of a confirmed smoke user
    SMOKE_TEST_PASSWORD    # that user's password

One-time setup (create the smoke user in the prod Supabase dashboard):
    Authentication → Users → Add user → check "Auto Confirm User" so the
    user can sign in without an email round-trip.

This script is intentionally narrow — it confirms the cross-origin auth
+ device + ledger write path is alive. It is not a substitute for the
pytest contract suite, which still owns RLS correctness.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
from supabase import create_client


def _load_dotenv() -> None:
    """Read SMOKE_* keys from a repo-root .env into os.environ if unset.

    No python-dotenv dep — we only need KEY=VALUE lines with optional
    surrounding quotes. Already-exported env vars win, so CI can override
    by exporting before invocation.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in os.environ:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv()


@dataclass(frozen=True)
class SmokeConfig:
    """Resolved env config for one smoke run."""

    api_url: str
    supabase_url: str
    supabase_anon_key: str
    email: str
    password: str


def main() -> None:
    """Drive the full smoke sequence and exit non-zero on first failure."""
    cfg = _load_config()
    device_id = f"smoke-{uuid.uuid4().hex[:8]}"
    print(f"→ prod API:    {cfg.api_url}")
    print(f"→ supabase:    {cfg.supabase_url}")
    print(f"→ device_id:   {device_id}")
    print(f"→ smoke user:  {cfg.email}")
    print()

    jwt = _step("sign in via Supabase", lambda: _sign_in(cfg))
    me = _step("GET /me", lambda: _get_me(cfg, jwt))

    if me.get("home_currency") is None:
        _step(
            "POST /auth/bootstrap (new smoke user)",
            lambda: _bootstrap(cfg, jwt, device_id, home_currency="USD"),
        )
    else:
        _step(
            "POST /auth/claim_device",
            lambda: _claim_device(cfg, jwt, device_id),
        )

    txn = _step(
        "POST /transactions/confirm",
        lambda: _create_transaction(cfg, jwt, device_id),
    )
    txn_id = txn["transaction"]["id"]

    _step(
        "GET /transactions/{id}",
        lambda: _read_transaction(cfg, jwt, device_id, txn_id),
    )

    _step(
        "DELETE /transactions/{id}",
        lambda: _delete_transaction(cfg, jwt, device_id, txn_id),
    )

    print()
    print("✓ All checks passed.")


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _load_config() -> SmokeConfig:
    """Read and validate env vars; exit with a clear message if any are missing."""
    required = (
        "SMOKE_API_URL",
        "SMOKE_SUPABASE_URL",
        "SMOKE_SUPABASE_ANON_KEY",
        "SMOKE_TEST_EMAIL",
        "SMOKE_TEST_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(
            "Missing required env vars: " + ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "See the module docstring for setup instructions.",
            file=sys.stderr,
        )
        sys.exit(2)
    return SmokeConfig(
        api_url=os.environ["SMOKE_API_URL"].rstrip("/"),
        supabase_url=os.environ["SMOKE_SUPABASE_URL"].rstrip("/"),
        supabase_anon_key=os.environ["SMOKE_SUPABASE_ANON_KEY"],
        email=os.environ["SMOKE_TEST_EMAIL"],
        password=os.environ["SMOKE_TEST_PASSWORD"],
    )


def _step(label, fn):
    """Time `fn`, print a green check on success, and exit on failure."""
    start = time.monotonic()
    try:
        result = fn()
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        print(f"  ✗ {label}  ({elapsed_ms:.0f} ms)  — {exc}")
        sys.exit(1)
    elapsed_ms = (time.monotonic() - start) * 1000
    print(f"  ✓ {label}  ({elapsed_ms:.0f} ms)")
    return result


def _sign_in(cfg: SmokeConfig) -> str:
    """Password sign-in via supabase-py; returns the user JWT."""
    client = create_client(cfg.supabase_url, cfg.supabase_anon_key)
    session = client.auth.sign_in_with_password(
        {"email": cfg.email, "password": cfg.password}
    ).session
    if session is None or not session.access_token:
        raise RuntimeError("sign_in_with_password returned no session")
    return session.access_token


def _get_me(cfg: SmokeConfig, jwt: str) -> dict:
    """GET /me — outside the device gate, so no X-Device-Id header."""
    return _request(cfg, "GET", "/me", jwt=jwt, device_id=None)


def _bootstrap(cfg: SmokeConfig, jwt: str, device_id: str, home_currency: str) -> dict:
    """First-time users_meta row insert."""
    return _request(
        cfg,
        "POST",
        "/auth/bootstrap",
        jwt=jwt,
        device_id=None,
        json={"device_id": device_id, "home_currency": home_currency},
    )


def _claim_device(cfg: SmokeConfig, jwt: str, device_id: str) -> dict:
    """Returning user — rotate active_device_id to ours."""
    return _request(
        cfg,
        "POST",
        "/auth/claim_device",
        jwt=jwt,
        device_id=None,
        json={"device_id": device_id},
    )


def _create_transaction(cfg: SmokeConfig, jwt: str, device_id: str) -> dict:
    """Confirm a synthetic transaction — uses a fresh client_request_id."""
    body = {
        "merchant": "Smoke Test Merchant",
        "amount": "1.23",
        "date": date.today().isoformat(),
        "category": "Other",
        "client_request_id": str(uuid.uuid4()),
    }
    return _request(
        cfg,
        "POST",
        "/transactions/confirm",
        jwt=jwt,
        device_id=device_id,
        json=body,
    )


def _read_transaction(cfg: SmokeConfig, jwt: str, device_id: str, txn_id: str) -> dict:
    """Read-back to confirm RLS lets us see what we just wrote."""
    return _request(
        cfg,
        "GET",
        f"/transactions/{txn_id}",
        jwt=jwt,
        device_id=device_id,
    )


def _delete_transaction(cfg: SmokeConfig, jwt: str, device_id: str, txn_id: str) -> None:
    """Tear down — leaves prod clean even if earlier steps left rows."""
    _request(
        cfg,
        "DELETE",
        f"/transactions/{txn_id}",
        jwt=jwt,
        device_id=device_id,
        expect_status=204,
    )


def _request(
    cfg: SmokeConfig,
    method: str,
    path: str,
    *,
    jwt: str,
    device_id: str | None,
    json: dict | None = None,
    expect_status: int | None = None,
) -> dict:
    """Single httpx call with Bearer + optional X-Device-Id; raises on bad status."""
    headers = {"Authorization": f"Bearer {jwt}"}
    if device_id is not None:
        headers["X-Device-Id"] = device_id
    url = f"{cfg.api_url}{path}"
    resp = httpx.request(method, url, headers=headers, json=json, timeout=15.0)
    expected = expect_status if expect_status is not None else 200
    if resp.status_code != expected:
        raise RuntimeError(
            f"{method} {path} → {resp.status_code} {resp.text[:200]}"
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


if __name__ == "__main__":
    main()
