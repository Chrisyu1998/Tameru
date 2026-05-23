"""Static contract: every `log_ai_call` site enumerates all required kwargs.

DESIGN.md §8.8 requires every `ai_call_log` row to carry the 10 fields
the table CHECK / column-NOT-NULL constraints expect. The runtime
integration tests (tests/integrations/test_categorize.py, test_chat_*,
test_card_lookup.py, test_memory_distill.py) already exercise each call
site through fixtures and assert per-field population — duplicating that
runtime coverage here would be expensive and brittle.

This test is the static complement: it walks `app/` for every call to
`log_ai_call(...)` and asserts the *call expression itself* names every
required keyword argument. A regression where a future PR drops a kwarg
("just temporarily, I'll add it back") fails the build immediately,
before runtime testing rolls the dice on whether the affected path is
exercised by the suite.

Optional fields (`error_code`) are excluded — they default to None at
the function signature. The list below mirrors `log_ai_call`'s
signature in `app/integrations/aicalllog.py`.

The audit set: every existing call site listed in the Day 24 prompt
under §3 must be visible to this walker.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"

_REQUIRED_LOG_AI_CALL_KWARGS = frozenset({
    "user_id",
    "provider",
    "model",
    "task_type",
    "prompt_version",
    "prompt_hash",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "success",
})

# Modules expected to call `log_ai_call`. Each is a known AI integration
# whose audit coverage Day 24 explicitly enumerated. The test fails if
# any of these has zero call sites OR the call-site walker turns up a
# call from a module not in this set (a new AI surface that didn't get
# reviewed for audit completeness).
_EXPECTED_CALLER_MODULES = frozenset({
    "app/agent/loop.py",
    "app/agent/memory.py",
    "app/integrations/gemini.py",
    "app/integrations/card_lookup.py",
})


def test_every_log_ai_call_site_passes_required_kwargs() -> None:
    """Every call to `log_ai_call(...)` in `app/` names all required kwargs.

    The test reads each .py file in `app/` and AST-walks for any Call
    whose `func` is `log_ai_call` (bare) or `aicalllog.log_ai_call`
    (attribute). Each found call must include every required kwarg by
    name. The test does not enforce values — runtime tests cover those.
    """
    sites = _collect_log_ai_call_sites()
    assert sites, "no `log_ai_call(...)` invocations found — pattern walker broken?"

    failures: list[str] = []
    for path, lineno, kwargs in sites:
        missing = _REQUIRED_LOG_AI_CALL_KWARGS - kwargs
        if missing:
            failures.append(
                f"{path.relative_to(APP_DIR.parent)}:{lineno} "
                f"missing required kwargs: {sorted(missing)}"
            )
    assert not failures, "log_ai_call invocations missing kwargs:\n" + "\n".join(failures)


def test_expected_caller_modules_are_all_present() -> None:
    """Every module Day 24's audit enumerated has at least one
    `log_ai_call` call site.

    If a refactor accidentally drops the call from e.g.
    `app/integrations/gemini.py`, this test surfaces the regression
    immediately rather than waiting for a per-integration runtime test
    to flake on the missing row.
    """
    sites = _collect_log_ai_call_sites()
    callers = {str(path.relative_to(APP_DIR.parent)) for path, _, _ in sites}
    missing = _EXPECTED_CALLER_MODULES - callers
    assert not missing, (
        "expected AI integration modules with no `log_ai_call` call sites: "
        f"{sorted(missing)}. Either the module no longer writes audit rows "
        "(invariant 14 violation) or this test's allowlist is stale."
    )


def test_no_new_log_ai_call_sites_outside_expected_modules() -> None:
    """New `log_ai_call` callers outside the expected set surface here.

    A new AI surface (e.g. a future receipt-parse pipeline) should
    explicitly land in the audit allowlist alongside its prompt-hash
    and rate-limiting work; otherwise an audit-completeness review for
    a new integration is silently skipped.
    """
    sites = _collect_log_ai_call_sites()
    callers = {str(path.relative_to(APP_DIR.parent)) for path, _, _ in sites}
    extras = callers - _EXPECTED_CALLER_MODULES
    # `app/integrations/aicalllog.py` itself is the definition site, not
    # a caller — it never appears in `_collect_log_ai_call_sites` (the
    # walker skips function definitions and tests via the `Call` filter).
    assert not extras, (
        "new `log_ai_call` caller(s) found outside the expected set: "
        f"{sorted(extras)}. Add them to _EXPECTED_CALLER_MODULES and "
        "confirm their audit completeness in tests/integrations/."
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _collect_log_ai_call_sites() -> list[tuple[Path, int, frozenset[str]]]:
    """Walk `app/` for every `log_ai_call(...)` call expression.

    Returns a list of `(path, lineno, kwargs)` triples. `kwargs` is the
    set of keyword names actually passed at the call site. Positional
    args are ignored — the function signature is keyword-only after the
    `user_jwt` arg, so any positional placement would already be a
    TypeError.
    """
    out: list[tuple[Path, int, frozenset[str]]] = []
    for path in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_log_ai_call(node.func):
                continue
            kwargs = frozenset(
                kw.arg for kw in node.keywords if kw.arg is not None
            )
            out.append((path, node.lineno, kwargs))
    return out


def _is_log_ai_call(func: ast.expr) -> bool:
    """Return True if `func` refers to `log_ai_call` (bare or attribute).

    Accepts both `log_ai_call(...)` and `aicalllog.log_ai_call(...)`
    forms. Anything else is a false positive — e.g. a method with the
    same suffix on an unrelated class.
    """
    if isinstance(func, ast.Name) and func.id == "log_ai_call":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "log_ai_call":
        return True
    return False
