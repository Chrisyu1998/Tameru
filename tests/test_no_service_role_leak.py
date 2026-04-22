"""Grep-style guard — CLAUDE.md invariant 1, DESIGN.md §9.1.

`supabase_admin()` and the raw `SUPABASE_SERVICE_ROLE_KEY` env var must not
reach any request-handling code path. Allowed importers: `app/cron/` (pg_cron
jobs) and `scripts/` (migration and administrative scripts). Everywhere else
in `app/` is a violation.

This is a test, not a real linter. Promote to a ruff custom rule if it ever
false-positives on us.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
ALLOWED_SUBDIRS = {"cron", "scripts"}  # scripts lives at repo root, kept here for parity

_FORBIDDEN_PATTERNS = [
    # Any import that pulls `supabase_admin` out of app.db, including renamed aliases.
    re.compile(r"^\s*from\s+app\.db\s+import\s+[^\n]*\bsupabase_admin\b", re.MULTILINE),
    # Wildcard import from app.db launders supabase_admin into the module namespace.
    re.compile(r"^\s*from\s+app\.db\s+import\s+\*", re.MULTILINE),
    # Anyone reaching for the service role env var directly.
    re.compile(r"\bSUPABASE_SERVICE_ROLE_KEY\b"),
]


def _is_allowed(path: Path) -> bool:
    rel = path.relative_to(APP_DIR)
    return len(rel.parts) >= 2 and rel.parts[0] in ALLOWED_SUBDIRS


def test_no_service_role_leak_in_app():
    # The definition site itself is allowed.
    definition = (APP_DIR / "db.py").resolve()

    offenders: list[tuple[Path, str]] = []
    for py in APP_DIR.rglob("*.py"):
        if py.resolve() == definition:
            continue
        if _is_allowed(py):
            continue
        text = py.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(text):
                offenders.append((py, pattern.pattern))

    assert not offenders, (
        "service-role leak: app/ code outside app/cron/ is referencing "
        "supabase_admin or SUPABASE_SERVICE_ROLE_KEY. Move the caller under "
        "app/cron/ or scripts/, or refactor to use supabase_for_user. "
        f"Offenders: {offenders}"
    )
