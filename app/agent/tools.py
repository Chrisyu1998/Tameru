"""Typed tools the Claude Haiku agent can call (Day 8 — calculate_total only).

DESIGN.md §7.2 mandates typed tools — no `run_query(sql)` surface, no SQL
generation. Each tool is a Python function callable with the user's JWT in
scope so RLS scopes the read automatically.

Day 8 ships one tool. Day 9 adds the rest of the read tools and the
propose_* write tools (no INSERT inside any tool — the proposal pattern is
CLAUDE.md invariant 8). The TOOL_REGISTRY is the seam Day 9 extends.

Aggregation note: PostgREST's Python client has no clean SUM. We fetch
matching rows and sum in Python with a hard cap (RESULT_ROW_CAP). At v1
scale (~10 invite-only users, ~thousands of rows/user) this is bounded
cheap. If a user ever crosses the cap, the tool returns `truncated: true`
and Claude is instructed (via the system prompt) to surface it. A SQL RPC
becomes worth it only when truncation actually fires for real users.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Callable

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.prompts.categories import ALLOWED_CATEGORIES

# Hard cap on rows fetched for an aggregation. Above this we still return
# the partial sum but flag the result as truncated so Claude can ask the
# user to narrow filters. Sized for v1 scale — a single user crossing 5k
# transactions in a single filter window is a future-Tameru problem.
RESULT_ROW_CAP = 5_000


CALCULATE_TOTAL_TOOL: dict[str, Any] = {
    "name": "calculate_total",
    "description": (
        "Sum the user's transactions matching optional filters. Returns the "
        "total amount and the count of transactions that contributed. Use "
        "for any 'how much did I spend' or aggregate-total question. All "
        "filters are optional; an unfiltered call totals everything."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category": {
                "type": "string",
                "enum": list(ALLOWED_CATEGORIES),
                "description": "Restrict to one category from the closed enum.",
            },
            "card_id": {
                "type": "string",
                "format": "uuid",
                "description": "Restrict to one card by its UUID.",
            },
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "Inclusive lower bound (YYYY-MM-DD).",
            },
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Inclusive upper bound (YYYY-MM-DD).",
            },
        },
    },
}


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def calculate_total(
    user: AuthedUser,
    *,
    category: str | None = None,
    card_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Implementation of the calculate_total tool.

    RLS on the `transactions` table (FOR ALL on user_id = auth.uid())
    scopes the read — the absence of a `WHERE user_id = ?` is intentional;
    the contract test in tests/contracts/test_rls.py is what guarantees
    that's safe.
    """
    client = supabase_for_user(user.jwt)
    query = client.table("transactions").select("amount")

    if category is not None:
        if category not in ALLOWED_CATEGORIES:
            # Reject out-of-enum categories at the Python layer; the model
            # is constrained by the input_schema enum but a malformed call
            # shouldn't hit Postgres with garbage.
            raise ValueError(f"category {category!r} not in ALLOWED_CATEGORIES")
        query = query.eq("category", category)
    if card_id is not None:
        query = query.eq("card_id", card_id)
    if date_from is not None:
        query = query.gte("date", _parse_date(date_from).isoformat())
    if date_to is not None:
        query = query.lte("date", _parse_date(date_to).isoformat())

    # Fetch one extra to detect truncation without a separate COUNT query —
    # same pattern app/services/transactions.py uses for has_more.
    resp = query.range(0, RESULT_ROW_CAP).execute()
    rows = resp.data or []
    truncated = len(rows) > RESULT_ROW_CAP
    if truncated:
        rows = rows[:RESULT_ROW_CAP]

    # Decimal sum — `numeric` columns come back as strings from Supabase.
    total = sum((Decimal(str(row["amount"])) for row in rows), Decimal("0"))

    return {
        "total": str(total),
        "count": len(rows),
        "truncated": truncated,
    }


# Tool name → (schema, executor). Day 9 extends this dict with the rest of
# the tool surface; the loop iterates it to build the `tools=` argument and
# to dispatch tool_use blocks. Keeping schemas and executors paired here
# means a tool that ships without one or the other can't slip through.
TOOL_REGISTRY: dict[str, tuple[dict[str, Any], Callable[..., Any]]] = {
    CALCULATE_TOTAL_TOOL["name"]: (CALCULATE_TOTAL_TOOL, calculate_total),
}


def tool_schemas() -> list[dict[str, Any]]:
    """The list passed as `tools=` to anthropic.messages.create()."""
    return [schema for schema, _ in TOOL_REGISTRY.values()]


def execute_tool(name: str, tool_input: dict[str, Any], user: AuthedUser) -> dict[str, Any]:
    """Dispatch a single tool_use block to its registered executor.

    Raises KeyError when `name` is unknown — the loop catches that and
    emits an `is_error` tool_result block so Claude can recover instead
    of crashing the turn.
    """
    schema, executor = TOOL_REGISTRY[name]
    return executor(user, **tool_input)
