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


@dataclass(frozen=True)
class TestUser:
    """Represent TestUser."""
    id: str
    email: str
    password: str
    jwt: str
    # Per-user device id baked into the fixture so tests can include the
    # `X-Device-Id` header that authenticated routes (post-Day-7) require.
    # Populated for the bootstrapped session-scoped users_a/_b; left None on
    # raw users (`user_unbootstrapped`) so auth-route tests can exercise the
    # pre-bootstrap state.
    device_id: str | None = None


@pytest.fixture(scope="session")
def supabase_env(_supabase_stack_ready) -> dict[str, str]:
    """Provide supabase env."""
    return {
        "url": os.environ["SUPABASE_URL"],
        "anon_key": os.environ["SUPABASE_ANON_KEY"],
        "service_role_key": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    }


@pytest.fixture(scope="session")
def admin_client(supabase_env) -> Client:
    """Provide admin client."""
    return create_client(supabase_env["url"], supabase_env["service_role_key"])


@pytest.fixture(scope="session")
def user_a(admin_client, supabase_env):
    """Provide user a."""
    raw = _make_user(admin_client, supabase_env["url"], supabase_env["anon_key"], "a")
    user = _bootstrap_user(raw, device_id=f"dev-a-{uuid.uuid4().hex[:8]}")
    yield user
    _delete_user(admin_client, user.id)


@pytest.fixture(scope="session")
def user_b(admin_client, supabase_env):
    """Provide user b."""
    raw = _make_user(admin_client, supabase_env["url"], supabase_env["anon_key"], "b")
    user = _bootstrap_user(raw, device_id=f"dev-b-{uuid.uuid4().hex[:8]}")
    yield user
    _delete_user(admin_client, user.id)


@pytest.fixture
def user_unbootstrapped(admin_client, supabase_env):
    """Function-scoped fresh user with no `users_meta` row — used by the
    auth-route tests that exercise the bootstrap / claim_device / 409
    paths. Cleaned up after each test so a single suite run can spin up
    several without bumping into stale state."""
    raw = _make_user(
        admin_client,
        supabase_env["url"],
        supabase_env["anon_key"],
        f"fresh-{uuid.uuid4().hex[:6]}",
    )
    yield raw
    _delete_user(admin_client, raw.id)


# Per-user card fixtures — shared across the RLS contract suite and the
# Day 5 transactions suite. `subscriptions` requires `card_id NOT NULL`, and
# `transactions.card_id` is the FK that the Day 5 confirm-path ownership
# check validates.
@pytest.fixture(scope="session")
def card_a(user_a) -> str:
    """Provide card a."""
    from app.db import supabase_for_user

    client = supabase_for_user(user_a.jwt)
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user_a.id,
                "name": "A card",
                # `issuer` is a closed CHECK enum since the Day 14 follow-up
                # migration (20260516140000_cards_uniqueness_by_issuer.sql).
                # All inserts here use canonical lowercase identifiers.
                "issuer": "chase",
                "program": "UR",
                "network": "visa",
                "last_four": "1111",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


@pytest.fixture(scope="session")
def card_b(user_b) -> str:
    """Provide card b."""
    from app.db import supabase_for_user

    client = supabase_for_user(user_b.jwt)
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user_b.id,
                "name": "B card",
                "issuer": "amex",
                "program": "MR",
                "network": "amex",
                "last_four": "2222",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


@pytest.fixture
def clean_memory(user_a):
    """Wipe user_a's user_memory + conversation_distillation_state rows.

    Day 16 memory tests are session-scoped on `user_a`, so rows leak
    between tests without a per-test cleanup. Tests opt in via
    `pytestmark = pytest.mark.usefixtures("clean_memory")` at module
    level so the cleanup only fires where it's actually needed.
    """
    from app.db import supabase_for_user

    client = supabase_for_user(user_a.jwt)
    # Order matters only loosely (no FKs between these tables); we wipe
    # conversation_distillation_state first because chat_messages is
    # what its predicate hangs off. chat_messages and chat_turn_trace
    # get wiped too — the piggyback predicate reads chat_messages, so
    # leaving stale rows from a previous test would make a later test's
    # "no piggyback expected" assertion false.
    client.table("conversation_distillation_state").delete().eq(
        "user_id", user_a.id
    ).execute()
    client.table("user_memory").delete().eq("user_id", user_a.id).execute()
    client.table("chat_messages").delete().eq("user_id", user_a.id).execute()
    client.table("chat_turn_trace").delete().eq("user_id", user_a.id).execute()
    yield


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

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
        status, _ = json.JSONDecoder().raw_decode(stdout[brace:])
    except json.JSONDecodeError:
        return
    for env_name, status_key in needed.items():
        if not os.environ.get(env_name) and status.get(status_key):
            os.environ[env_name] = status[status_key]

def _assert_local_supabase() -> None:
    """Support assert local supabase."""
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

@pytest.fixture(scope="session")
def _supabase_stack_ready() -> None:
    """Gate for every fixture/test that actually talks to Supabase.

    Tests that don't request this (directly or transitively) — e.g. the
    static import-graph check in `test_no_service_role_leak.py` — run
    without a local stack, which keeps pure-unit tests portable.
    """
    _populate_env_from_supabase_status()
    _assert_local_supabase()

def _make_user(admin_client: Client, anon_url: str, anon_key: str, tag: str) -> TestUser:
    """Support make user."""
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

def _bootstrap_user(user: TestUser, device_id: str, currency: str = "USD") -> TestUser:
    """Insert the user's `users_meta` row so the Day 7 device gate accepts
    requests from this fixture. We bypass the HTTP layer and write
    directly via the user's RLS-scoped client — same write the
    `/auth/bootstrap` route makes, just without the round trip."""
    from app.db import supabase_for_user

    client = supabase_for_user(user.jwt)
    client.table("users_meta").insert(
        {
            "user_id": user.id,
            "active_device_id": device_id,
            "home_currency": currency,
        }
    ).execute()
    return TestUser(
        id=user.id,
        email=user.email,
        password=user.password,
        jwt=user.jwt,
        device_id=device_id,
    )

def _delete_user(admin_client: Client, user_id: str) -> None:
    """Support delete user."""
    try:
        admin_client.auth.admin.delete_user(user_id)
    except Exception:
        # Best-effort teardown — don't fail the session if cleanup hiccups.
        pass
