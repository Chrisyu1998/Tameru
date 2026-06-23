"""Repository-wide docstring doctrine guard.

Every Python function, class, test, and private helper should explain its
contract at the definition site. This keeps tests readable at a glance and
keeps API/tool boundaries clear without relying on nearby prose comments.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# CLAUDE.md states the doctrine repo-wide; eval.py and scripts/ used to
# escape the scan (audit P3-19).
SCANNED_ROOTS = (REPO_ROOT / "app", REPO_ROOT / "tests", REPO_ROOT / "scripts")
SCANNED_FILES = (REPO_ROOT / "eval.py",)


def test_python_definitions_have_docstrings() -> None:
    """Require docstrings on app/test definitions, including private helpers."""
    missing: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if ast.get_docstring(node):
                continue
            rel = path.relative_to(REPO_ROOT)
            missing.append(f"{rel}:{node.lineno} {_definition_kind(node)} {node.name}")

    assert not missing, "missing docstrings:\n" + "\n".join(missing)


def test_private_helpers_stay_below_primary_definitions() -> None:
    """Require top-level private helper functions to live at file bottoms."""
    offenders: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        definitions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        last_primary_line = max(
            (
                node.lineno
                for node in definitions
                if not (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("_")
                )
            ),
            default=0,
        )
        for node in definitions:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_"):
                continue
            if node.lineno < last_primary_line:
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{node.lineno} helper {node.name}")

    assert not offenders, "private helpers must live below primary definitions:\n" + "\n".join(
        offenders
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _iter_python_files() -> list[Path]:
    """Return Python source files covered by the docstring doctrine."""
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        files.extend(
            path
            for path in root.rglob("*.py")
            if path.name != "__init__.py" and path.resolve() != Path(__file__).resolve()
        )
    files.extend(path for path in SCANNED_FILES if path.exists())
    return sorted(files)

def _definition_kind(node: ast.AST) -> str:
    """Return a human-readable kind for an AST definition node."""
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, ast.AsyncFunctionDef):
        return "async function"
    return "function"
