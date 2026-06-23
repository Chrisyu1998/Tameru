"""Mint a throwaway Supabase user on the local stack and print its JWT.

Used for manual smoke tests of FastAPI auth-protected routes:

    .venv/bin/python scripts/mint_test_jwt.py
    # copy the token that prints
    curl -s http://localhost:8000/me -H "Authorization: Bearer <paste>"

Reads connection details from `supabase status -o json`, so `supabase start`
must be running. The user is created with a random email under
`@tameru.local` and is NOT deleted on exit — Supabase-side clean-up is
manual (or `supabase db reset`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid

from supabase import create_client


def main() -> None:
    """Mint a confirmed throwaway local-Supabase user and print its JWT."""
    s = _load_local_status()
    admin = create_client(s["API_URL"], s["SERVICE_ROLE_KEY"])

    email = f"manual-{uuid.uuid4().hex[:8]}@tameru.local"
    password = f"pw-{uuid.uuid4().hex}"
    admin.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )

    anon = create_client(s["API_URL"], s["ANON_KEY"])
    session = anon.auth.sign_in_with_password(
        {"email": email, "password": password}
    ).session

    print(f"# email: {email}", file=sys.stderr)
    print(f"# user_id: {session.user.id}", file=sys.stderr)
    print(session.access_token)


def _load_local_status() -> dict:
    """Return `supabase status -o json` as a dict, exiting if the stack is down."""
    raw = subprocess.check_output(
        ["supabase", "status", "-o", "json"], text=True
    )
    brace = raw.find("{")
    if brace < 0:
        print("supabase status did not return JSON; is the stack running?", file=sys.stderr)
        sys.exit(1)
    return json.loads(raw[brace:])


if __name__ == "__main__":
    main()
