"""Test fixtures for the RLS contract suite.

Tests that touch auth or RLS must never reach a hosted Supabase project.
The `_supabase_stack_ready` fixture asserts `SUPABASE_URL` is localhost and
auto-populates env vars from `supabase status -o json`. Fixtures that talk
to the stack depend on it; pure-unit tests (e.g. the import-graph check in
`test_no_service_role_leak.py`) don't, so they run without a local stack.

Common developer flow:

    supabase start && supabase db reset && pytest
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import pytest
from supabase import Client, create_client

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# Module-level default so every test process has a Gemini model resolved
# without requiring each developer to set an env var manually. Uses the
# stable GA model — smoke tests against the preview model are opt-in via
# an explicit `GEMINI_MODEL=gemini-3.1-flash-lite-preview` override.
# `setdefault` respects any pre-existing env from CI / .env / shell.
os.environ.setdefault("GEMINI_MODEL_DEFAULT", "gemini-2.5-flash")


def _populate_env_from_supabase_status() -> None:
    """Fill in SUPABASE_* env vars from the local CLI if they're missing.

    Only runs when variables are absent — a real CI env (with explicit vars)
    is never overwritten. Silently skips if the CLI isn't available; the
    assertion in `_assert_local_supabase` will then produce the actionable
    error.
    """
    needed = {
        "SUPABASE_URL": "API_URL",
        "SUPABASE_ANON_KEY": "ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY": "SERVICE_ROLE_KEY",
    }
    if all(os.environ.get(env_name) for env_name in needed):
        return
    try:
        result = subprocess.run(
            ["supabase", "status", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return
    # `supabase status` sometimes prints a banner before the JSON; find the
    # first `{` to be robust.
    stdout = result.stdout
    brace = stdout.find("{")
    if brace < 0:
        return
    try:
        status = json.loads(stdout[brace:])
    except json.JSONDecodeError:
        return
    for env_name, status_key in needed.items():
        if not os.environ.get(env_name) and status.get(status_key):
            os.environ[env_name] = status[status_key]


def _assert_local_supabase() -> None:
    url = os.environ.get("SUPABASE_URL", "")
    host = urlparse(url).hostname or ""
    if host not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"SUPABASE_URL must point at a local stack, got {url!r}. "
            "Run `supabase start` (and `supabase db reset` to apply "
            "migrations) before running tests. The RLS contract suite "
            "creates and deletes users; running it against the hosted "
            "project would pollute real data."
        )


@dataclass(frozen=True)
class TestUser:
    id: str
    email: str
    password: str
    jwt: str


@pytest.fixture(scope="session")
def _supabase_stack_ready() -> None:
    """Gate for every fixture/test that actually talks to Supabase.

    Tests that don't request this (directly or transitively) — e.g. the
    static import-graph check in `test_no_service_role_leak.py` — run
    without a local stack, which keeps pure-unit tests portable.
    """
    _populate_env_from_supabase_status()
    _assert_local_supabase()


@pytest.fixture(scope="session")
def supabase_env(_supabase_stack_ready) -> dict[str, str]:
    return {
        "url": os.environ["SUPABASE_URL"],
        "anon_key": os.environ["SUPABASE_ANON_KEY"],
        "service_role_key": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    }


@pytest.fixture(scope="session")
def admin_client(supabase_env) -> Client:
    return create_client(supabase_env["url"], supabase_env["service_role_key"])


def _make_user(admin_client: Client, anon_url: str, anon_key: str, tag: str) -> TestUser:
    email = f"rls-{tag}-{uuid.uuid4().hex[:12]}@tameru.local"
    password = f"test-{uuid.uuid4().hex}"
    created = admin_client.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    user_id = created.user.id
    anon = create_client(anon_url, anon_key)
    session = anon.auth.sign_in_with_password({"email": email, "password": password})
    jwt = session.session.access_token
    return TestUser(id=user_id, email=email, password=password, jwt=jwt)


def _delete_user(admin_client: Client, user_id: str) -> None:
    try:
        admin_client.auth.admin.delete_user(user_id)
    except Exception:
        # Best-effort teardown — don't fail the session if cleanup hiccups.
        pass


@pytest.fixture(scope="session")
def user_a(admin_client, supabase_env):
    user = _make_user(admin_client, supabase_env["url"], supabase_env["anon_key"], "a")
    yield user
    _delete_user(admin_client, user.id)


@pytest.fixture(scope="session")
def user_b(admin_client, supabase_env):
    user = _make_user(admin_client, supabase_env["url"], supabase_env["anon_key"], "b")
    yield user
    _delete_user(admin_client, user.id)


# Per-user card fixtures — shared across the RLS contract suite and the
# Day 5 transactions suite. `subscriptions` requires `card_id NOT NULL`, and
# `transactions.card_id` is the FK that the Day 5 confirm-path ownership
# check validates.
@pytest.fixture(scope="session")
def card_a(user_a) -> str:
    from app.db import supabase_for_user

    client = supabase_for_user(user_a.jwt)
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user_a.id,
                "name": "A card",
                "issuer": "Chase",
                "program": "UR",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


@pytest.fixture(scope="session")
def card_b(user_b) -> str:
    from app.db import supabase_for_user

    client = supabase_for_user(user_b.jwt)
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user_b.id,
                "name": "B card",
                "issuer": "Amex",
                "program": "MR",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]
