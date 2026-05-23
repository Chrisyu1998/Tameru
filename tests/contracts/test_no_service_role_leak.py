"""Grep-style guard — CLAUDE.md invariant 1, DESIGN.md §9.1.

`supabase_admin()` and the raw `SUPABASE_SERVICE_ROLE_KEY` env var must not
reach any request-handling code path. Allowed by directory: `app/cron/`
(pg_cron and scheduled jobs) and `scripts/` (migration and administrative
scripts). Allowed by per-file exception (with rationale comments): the
unsubscribe and Resend-webhook routes, both of which CLAUDE.md invariant 1
explicitly admits as sanctioned service-role callers because the inbound
request carries no user JWT by design (Day 25, DESIGN.md §6.4). Everywhere
else in `app/` is a violation.

When adding to `ALLOWED_FILES`, follow the same discipline as
`ALLOWED_DIRECT_WRITE_TOOLS` in the tool-write invariant test: paste a
short rationale next to the entry naming the invariant clause that admits
the exception. The test passing without the rationale is fine; the
rationale is for humans reviewing the diff.

This is a test, not a real linter. Promote to a ruff custom rule if it ever
false-positives on us.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent.parent / "app"
ALLOWED_SUBDIRS = {"cron", "scripts"}  # scripts lives at repo root, kept here for parity

# Per-file exceptions — CLAUDE.md invariant 1 expressly admits these as
# service-role callers because the inbound request has no user JWT in
# scope (one-click unsubscribe per RFC 8058, Resend webhook). Paths are
# relative to APP_DIR.
ALLOWED_FILES = {
    Path("routes/unsubscribe.py"),       # invariant 1 caller #3 surface
    Path("routes/webhooks_resend.py"),   # invariant 1 caller #4
    # main.py is the web-process boot-validation surface. It lists the
    # service-role env var name in _REQUIRED_ENV_VARS so a deploy that
    # forgets the key crashes loudly at startup rather than 500'ing
    # every unsubscribe and bounce-webhook request post-verification
    # (Codex 2026-05-23 P2). Mentioning the literal var name in
    # _REQUIRED_ENV_VARS is unavoidable — the boot check needs the
    # exact string — and is structurally legitimate because the two
    # routes admitted above are this file's downstream callers.
    Path("main.py"),
}

_FORBIDDEN_PATTERNS = [
    # Any import that pulls `supabase_admin` out of app.db, including renamed aliases.
    re.compile(r"^\s*from\s+app\.db\s+import\s+[^\n]*\bsupabase_admin\b", re.MULTILINE),
    # Wildcard import from app.db launders supabase_admin into the module namespace.
    re.compile(r"^\s*from\s+app\.db\s+import\s+\*", re.MULTILINE),
    # Anyone reaching for the service role env var directly.
    re.compile(r"\bSUPABASE_SERVICE_ROLE_KEY\b"),
]


def test_no_service_role_leak_in_app():
    # The definition site itself is allowed.
    """Verify that no service role leak in app."""
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
        "app/cron/ or scripts/, or — if the file is a sanctioned service-role "
        "caller per CLAUDE.md invariant 1 — add its path to ALLOWED_FILES "
        "with a rationale comment. "
        f"Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _is_allowed(path: Path) -> bool:
    """Allow by parent-directory rule or by per-file allowlist."""
    rel = path.relative_to(APP_DIR)
    if len(rel.parts) >= 2 and rel.parts[0] in ALLOWED_SUBDIRS:
        return True
    if rel in ALLOWED_FILES:
        return True
    return False
