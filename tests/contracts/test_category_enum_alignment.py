"""Structural guard against frontend/backend category-enum drift.

The frontend (`frontend/src/lib/categories.ts`) and the backend
(`app/prompts/categories.py`) share a single closed-enum category list.
Backend is the source of truth — DESIGN.md §6.2's card-reward multiplier
matching is built on the MCC-aligned taxonomy in `ALLOWED_CATEGORIES`.

The original Day 10b blocking bug was exactly this drift: the Lovable-
imported frontend list contained `Transportation`/`Entertainment`/etc.
without the backend's `Coffee Shops`/`Gas`/`Transit`/`Streaming`/
`Drugstores`/`Home` entries. The result was a 422 on every
`/transactions/confirm` whose category the frontend didn't know about,
plus a silent coercion to `Groceries` on any edit.

This test parses the TS source for the literal array, normalizes the
strings, and asserts equality with `ALLOWED_CATEGORIES`. We parse rather
than mirror because mirroring would just move the source-of-truth
problem one level up — the TS file is what the bundler reads, and that
is what must be correct.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.prompts.categories import ALLOWED_CATEGORIES


CATEGORIES_TS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "lib"
    / "categories.ts"
)


def test_frontend_categories_match_backend() -> None:
    """Parse `CATEGORIES` from categories.ts and compare to ALLOWED_CATEGORIES.

    Both order and membership must match exactly. The frontend uses
    `as const` to derive a tuple type from the array literal, so order
    is part of the contract — a UI control that reads `CATEGORIES`
    rendering options in a different order than the backend would
    surprise users when the backend re-categorizes their rows.
    """
    source = CATEGORIES_TS.read_text()

    # Match: export const CATEGORIES = [ "Foo", "Bar", ... ] as const;
    match = re.search(
        r"export\s+const\s+CATEGORIES\s*=\s*\[(.*?)\]\s*as\s+const",
        source,
        re.DOTALL,
    )
    assert match, (
        "Could not find `export const CATEGORIES = [...] as const` in "
        f"{CATEGORIES_TS}. The contract test relies on that exact shape; "
        "if the declaration moved, update this test or restore the shape."
    )
    body = match.group(1)
    frontend_list = tuple(re.findall(r'"([^"]+)"', body))

    backend_list = ALLOWED_CATEGORIES

    assert frontend_list == backend_list, (
        "Frontend CATEGORIES drifted from backend ALLOWED_CATEGORIES.\n"
        f"  frontend: {frontend_list}\n"
        f"  backend : {backend_list}\n"
        "Fix: mirror backend exactly in frontend/src/lib/categories.ts "
        "and update CATEGORY_TINT / CATEGORY_SKETCH for any new entries. "
        "If the backend changed, also bump PROMPT_VERSION in "
        "app/prompts/categorize.py."
    )
