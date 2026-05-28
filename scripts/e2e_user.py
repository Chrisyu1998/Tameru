"""Ephemeral E2E user lifecycle (Day 28).

Mints and tears down a per-run Playwright test user against the prod
Supabase project. Lives in `scripts/` so the leak-guard at
`tests/contracts/test_no_service_role_leak.py` already excludes it
(sanctioned service-role caller — no user JWT in scope by construction,
CLAUDE.md invariant 1).

Two subcommands:

    python scripts/e2e_user.py create
        Mints e2e+<unix_ts>-<rand>@tameru.xyz with a random password and
        auto-confirms the email. Does NOT pre-insert a `users_meta` row
        — `home_currency` is NOT NULL DEFAULT 'USD' (migration
        20260421120700) and the BEFORE UPDATE immutability trigger
        blocks any later swap, so any pre-seed would mark the user as
        already-onboarded and `01-signup.spec.ts` could never reach
        the currency step. The bootstrap call in CurrencyStep creates
        the row with the user's chosen currency in the same transaction
        that marks them onboarded. Prints THREE lines to stdout:

            E2E_TEST_EMAIL=<email>
            E2E_TEST_PASSWORD=<password>
            E2E_USER_ID=<uuid>

        CI captures these via stdout redirection (no `tee` — the
        credentials must not echo into the run log; see ci.yml notes).

        Trade-off: the ephemeral user fires PostHog events during the
        signup spec (the SDK opts in once /me confirms the default
        `analytics_opted_out=false`). Bounded to ~10–20 events per CI
        run, attributable to the `e2e+<ts>-<rand>@tameru.xyz` email,
        filterable from dashboards. The user is deleted at teardown.

    python scripts/e2e_user.py delete --user-id=<uuid>
        auth.admin.delete_user(uuid). The FK ON DELETE CASCADE on every
        user-content table to auth.users cleans up rows automatically;
        no per-table delete required. `ai_call_log` rows belonging to
        the user are retained — they're audit trail even for E2E runs,
        and at ~10 calls/run they don't move the dashboard.

Required env vars (mirrors scripts/smoke_prod.py's SMOKE_* namespacing
so the prod values don't collide with a developer's local `.env`,
which under the standard Supabase CLI workflow already holds the
local-stack `supabase-demo` service-role key):

    SMOKE_SUPABASE_URL                  # e.g. https://bvehjjrtcnnhjmsyoheb.supabase.co
    SMOKE_SUPABASE_SERVICE_ROLE_KEY     # prod service-role JWT (admin scope)
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

from supabase import create_client


_E2E_EMAIL_DOMAIN = "tameru.xyz"


def main() -> None:
    """Dispatch to the create / delete subcommand."""
    _load_dotenv()
    parser = argparse.ArgumentParser(
        description="Create or delete the per-run E2E test user.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create", help="Mint a fresh E2E user and print creds.")
    delete = sub.add_parser("delete", help="Delete the E2E user by id.")
    delete.add_argument(
        "--user-id",
        required=True,
        help="auth.users uuid to delete (printed as E2E_USER_ID on create).",
    )
    args = parser.parse_args()

    if args.cmd == "create":
        _create()
    elif args.cmd == "delete":
        _delete(args.user_id)
    else:  # pragma: no cover — argparse guarantees required dest.
        parser.error(f"unknown command: {args.cmd}")


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _create() -> None:
    """Mint a fresh auth.users row, print three KEY=VALUE lines.

    Deliberately writes nothing to `users_meta` — see module docstring.
    The signup spec drives onboarding through CurrencyStep, which
    creates the row server-side via /auth/bootstrap.
    """
    admin = _admin_client()
    email = f"e2e+{int(time.time())}-{secrets.token_hex(3)}@{_E2E_EMAIL_DOMAIN}"
    password = secrets.token_urlsafe(24)

    created = admin.auth.admin.create_user(
        {
            "email": email,
            "password": password,
            # Auto-confirm — no email round-trip, no Resend send (the E2E
            # user must not trigger real outbound mail).
            "email_confirm": True,
            # user_metadata is client-readable; we keep nothing in here.
            "user_metadata": {"created_by": "e2e_user.py"},
        }
    )
    user = getattr(created, "user", None)
    if user is None or not getattr(user, "id", None):
        raise RuntimeError(f"admin.create_user returned no user for {email}")
    user_id = str(user.id)

    # Stdout contract is exactly three KEY=VALUE lines for the CI step
    # to consume. The CI step redirects stdout to a file (no `tee`)
    # AND masks each value with ::add-mask:: so neither the email nor
    # the password ends up in the run log. Anything else (banners,
    # warnings) goes to stderr so the capture stays clean. The
    # `✓ minted` line below intentionally does NOT name the email or
    # the password — even on stderr, an unexpected log capture path
    # would expose the credentials otherwise.
    print(f"E2E_TEST_EMAIL={email}")
    print(f"E2E_TEST_PASSWORD={password}")
    print(f"E2E_USER_ID={user_id}")
    print(f"✓ minted E2E user (id={user_id})", file=sys.stderr)


def _delete(user_id: str) -> None:
    """Best-effort delete; never fails the build on a missing user.

    Idempotency matters here because the CI step runs with `if: always()`
    so a Playwright failure still triggers teardown. A second teardown
    pass (e.g. on a retried job) would otherwise 404 and surface as a
    confusing red step.
    """
    admin = _admin_client()
    try:
        admin.auth.admin.delete_user(user_id)
        print(f"✓ deleted E2E user {user_id}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — script-level catch-all is fine.
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            print(
                f"· E2E user {user_id} already gone; skipping",
                file=sys.stderr,
            )
            return
        print(f"✗ delete_user({user_id}) failed: {exc}", file=sys.stderr)
        sys.exit(1)


def _admin_client():
    """Construct the service-role Supabase client used for admin ops.

    `SMOKE_SUPABASE_SERVICE_ROLE_KEY` is namespaced with the same
    SMOKE_* prefix as `SMOKE_SUPABASE_URL` and `SMOKE_SUPABASE_ANON_KEY`
    so the prod value sits next to its siblings in `.env` and doesn't
    collide with the bare `SUPABASE_SERVICE_ROLE_KEY` that the local
    Supabase CLI stack (`supabase start`) plants in many developer
    environments.
    """
    url = _require("SMOKE_SUPABASE_URL").rstrip("/")
    key = _require("SMOKE_SUPABASE_SERVICE_ROLE_KEY")
    return create_client(url, key)


def _require(name: str) -> str:
    """Read a required env var; exit non-zero with a clear message if unset."""
    value = os.environ.get(name)
    if not value:
        print(
            f"Missing required env var: {name}. See scripts/e2e_user.py"
            " docstring for the full list.",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


def _load_dotenv() -> None:
    """Mirror scripts/smoke_prod.py's bare KEY=VALUE .env loader.

    Already-exported env vars win so CI can override by exporting before
    invocation.
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


if __name__ == "__main__":
    main()
