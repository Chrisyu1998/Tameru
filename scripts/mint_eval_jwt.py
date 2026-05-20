"""Provision the eval user on the local Supabase stack and print a JWT.

    .venv/bin/python scripts/mint_eval_jwt.py
    # token prints to stdout; metadata (email, user_id) to stderr

Used by `eval.py` at startup and by CI's `eval-gate` job. Idempotent —
the eval user (`eval@tameru.internal`) is created once and reused across
runs. The companion script `scripts/seed_eval_fixtures.py` runs the
deterministic card/transaction/subscription seed for the same user.

CLAUDE.md invariant 1 holds: the printed JWT is a real Supabase user
JWT, not a service-role bypass. Every call to `run_turn()` under it
exercises the production RLS path.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Direct invocation (`python scripts/mint_eval_jwt.py`) puts scripts/ on
# sys.path, not the repo root — so `import scripts` would fail. Prepend
# the repo root so the `scripts` package resolves either way (direct run
# or `python -m scripts.mint_eval_jwt`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._eval_setup import EVAL_USER_EMAIL, ensure_eval_user_jwt  # noqa: E402


def main() -> None:
    """Mint and print a fresh JWT for the eval user."""
    jwt, user_id = ensure_eval_user_jwt()
    print(f"# email: {EVAL_USER_EMAIL}", file=sys.stderr)
    print(f"# user_id: {user_id}", file=sys.stderr)
    print(jwt)


if __name__ == "__main__":
    main()
