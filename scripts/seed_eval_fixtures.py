"""Seed the deterministic card / transaction / subscription fixtures
the YAML eval corpora reference.

    .venv/bin/python scripts/seed_eval_fixtures.py

Idempotent — re-runs are safe and produce no duplicates. Reads pinned
fixture data from `scripts._eval_setup`.

Important: the multi-hop YAML rows in `evals/multi_hop.yaml` pin exact
`expected_answer_value_usd` values that depend on the fixture totals
defined in `scripts._eval_setup._FIXTURE_TRANSACTIONS`. If you change
totals here, update the matching YAML rows or multi-hop's final-answer
check will spuriously fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Direct invocation (`python scripts/seed_eval_fixtures.py`) puts
# scripts/ on sys.path, not the repo root — so `import scripts` would
# fail. Prepend the repo root so the `scripts` package resolves either
# way (direct run or `python -m scripts.seed_eval_fixtures`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._eval_setup import (  # noqa: E402
    EVAL_USER_EMAIL,
    ensure_eval_user_jwt,
    seed_fixtures,
)


def main() -> None:
    """Provision the eval user, then seed the YAML-referenced fixtures."""
    jwt, user_id = ensure_eval_user_jwt()
    print(f"# eval user: {EVAL_USER_EMAIL} ({user_id})", file=sys.stderr)
    result = seed_fixtures(jwt, user_id)
    print(f"# cards seeded: {sorted(result['cards'])}", file=sys.stderr)
    print("# seed complete", file=sys.stderr)


if __name__ == "__main__":
    main()
