"""Typed tools the Claude Haiku agent can call.

DESIGN.md §7.2 mandates typed tools — no `run_query(sql)` surface, no SQL
generation. Each tool is a Python function callable with the user's JWT in
scope so RLS scopes the read automatically.

Day 9a ships the read surface (`get_transactions`, `calculate_total`,
`get_subscriptions`, `get_spending_summary`, `get_cards`). All five share
the `TransactionFilters` filter shape where they overlap, routed through
`apply_transaction_filters` in `app/services/transactions.py` so the
HTTP-endpoint filter list and the agent-tool filter list cannot drift.

Day 9b will add `propose_transaction` and `set_goal`; Day 14 / Day 19 add
`propose_card` / `propose_subscription`. Each of those days extends
`TOOL_REGISTRY` — the registry is the only seam Claude sees, so a tool
that isn't registered there is invisible to the model.

Aggregation note: PostgREST's Python client has no clean SUM / GROUP BY.
For `calculate_total` and `get_spending_summary` we fetch matching rows
and aggregate in Python under a hard cap (`RESULT_ROW_CAP`). At v1 scale
(~10 invite-only users, ~thousands of rows/user) this is bounded cheap.
If a user ever crosses the cap, the tool returns `truncated: true` and
Claude is instructed (via the system prompt) to surface it. A SQL RPC
becomes worth it only when truncation actually fires for real users.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.models.transactions import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TransactionFilters,
)
from app.prompts.categories import ALLOWED_CATEGORIES
from app.services.transactions import apply_transaction_filters, list_transactions

# Hard cap on rows fetched for an aggregation. Above this we still return
# the partial sum but flag the result as truncated so Claude can ask the
# user to narrow filters. Sized for v1 scale — a single user crossing 5k
# transactions in a single filter window is a future-Tameru problem.
RESULT_ROW_CAP = 5_000

# Hard cap on list-tool outputs. `get_subscriptions` and `get_cards` are
# normally bounded by user count (subs <50, cards <10), but a defensive
# cap keeps a runaway tool call from blowing the context budget.
SUBSCRIPTIONS_ROW_CAP = 200


class CalculateTotalRequest(BaseModel):
    """Tool input for `calculate_total`.

    Example request:
        {"category": "Dining", "date_from": "2026-03-01", "date_to": "2026-03-31"}

    `user` is injected by the server-side loop, not supplied by Claude.
    """

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(default=None, description="Closed-enum category filter.")
    card_id: str | None = Field(default=None, description="Card UUID filter.")
    merchant_contains: str | None = Field(
        default=None,
        description="Case-insensitive merchant substring filter.",
    )
    date_from: _dt.date | None = Field(default=None, description="Inclusive date lower bound.")
    date_to: _dt.date | None = Field(default=None, description="Inclusive date upper bound.")
    amount_min: Decimal | None = Field(default=None, description="Inclusive amount lower bound.")
    amount_max: Decimal | None = Field(default=None, description="Inclusive amount upper bound.")

    @field_validator("category")
    @classmethod
    def _category_is_allowed(cls, value: str | None) -> str | None:
        """Reject categories outside Tameru's closed enum."""
        if value is not None and value not in ALLOWED_CATEGORIES:
            raise ValueError(f"category {value!r} is not in the closed enum")
        return value


class CalculateTotalResponse(BaseModel):
    """Tool result for `calculate_total`.

    Example response:
        {"total": "123.45", "count": 8, "truncated": false}
    """

    total: str
    count: int
    truncated: bool


class GetTransactionsRequest(CalculateTotalRequest):
    """Tool input for `get_transactions`.

    Example request:
        {"merchant_contains": "coffee", "limit": 10, "offset": 0}
    """

    limit: int = Field(default=DEFAULT_LIMIT, ge=1)
    offset: int = Field(default=0, ge=0)


class GetTransactionsResponse(BaseModel):
    """Tool result for `get_transactions`.

    Example response:
        {"items": [{"id": "...", "merchant": "Coffee Bar"}], "has_more": false}
    """

    items: list[dict[str, Any]]
    has_more: bool


class GetSubscriptionsRequest(BaseModel):
    """Tool input for `get_subscriptions`.

    Example request:
        {"status": "active"}
    """

    model_config = ConfigDict(extra="forbid")

    status: str | None = None

    @field_validator("status")
    @classmethod
    def _status_is_allowed(cls, value: str | None) -> str | None:
        """Reject subscription statuses outside the SQL enum."""
        allowed = {"active", "paused", "cancelled"}
        if value is not None and value not in allowed:
            raise ValueError(f"status {value!r} is not in {sorted(allowed)}")
        return value


class GetSubscriptionsResponse(BaseModel):
    """Tool result for `get_subscriptions`.

    Example response:
        {"items": [{"name": "Netflix", "status": "active"}], "truncated": false}
    """

    items: list[dict[str, Any]]
    truncated: bool


class GetSpendingSummaryRequest(BaseModel):
    """Tool input for `get_spending_summary`.

    Example request:
        {"months": 3}
    """

    model_config = ConfigDict(extra="forbid")

    months: int = Field(default=1)


class SpendingSummaryRow(BaseModel):
    """One category row inside a spending-summary tool response."""

    category: str
    total: str
    count: int


class GetSpendingSummaryResponse(BaseModel):
    """Tool result for `get_spending_summary`.

    Example response:
        {
            "window_start": "2026-03-01",
            "window_months": 3,
            "breakdown": [{"category": "Dining", "total": "123.45", "count": 8}],
            "truncated": false
        }
    """

    window_start: str
    window_months: int
    breakdown: list[SpendingSummaryRow]
    truncated: bool


class GetCardsResponse(BaseModel):
    """Tool result for `get_cards`.

    Example response:
        {"items": [{"id": "...", "name": "Amex Gold", "active": true}]}
    """

    items: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Shared input schema — every transaction-filter tool uses this shape.
# ---------------------------------------------------------------------------
#
# Defining the JSON schema once and reusing it across `get_transactions` and
# `calculate_total` is the schema-side mirror of the `apply_transaction_filters`
# service-side dedup. Same filter set, two outputs (rows vs total).

_TRANSACTION_FILTER_PROPERTIES: dict[str, Any] = {
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
    "merchant_contains": {
        "type": "string",
        "description": (
            "Case-insensitive substring match on merchant. Use for "
            "disambiguation when the user mentions a merchant by partial "
            "name (e.g. 'coffee', 'trader joe')."
        ),
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
    "amount_min": {
        "type": "number",
        "description": "Inclusive lower bound on amount.",
    },
    "amount_max": {
        "type": "number",
        "description": "Inclusive upper bound on amount.",
    },
}


CALCULATE_TOTAL_TOOL: dict[str, Any] = {
    "name": "calculate_total",
    "description": (
        "Sum the user's transactions matching optional filters. Returns the "
        "total amount and the count of transactions that contributed. Use "
        "for any aggregate question — 'how much did I spend', 'what's my "
        "total at X', monthly totals. All filters are optional; an "
        "unfiltered call totals everything. Prefer this over get_transactions "
        "when the user wants a sum, not a list."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(_TRANSACTION_FILTER_PROPERTIES),
    },
}


GET_TRANSACTIONS_TOOL: dict[str, Any] = {
    "name": "get_transactions",
    "description": (
        "Return a list of the user's transactions matching optional filters. "
        "Use when the user wants to see individual rows, find a specific "
        "transaction, or disambiguate a vague reference like 'that $10 "
        "coffee from last week.' Prefer calculate_total when the user wants "
        "a sum, not a list. Results are date-ordered (newest first) and "
        "capped at 500 rows; large result sets include has_more=true."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_TRANSACTION_FILTER_PROPERTIES,
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "description": (
                    f"Max rows to return (default {DEFAULT_LIMIT}, hard cap "
                    f"{MAX_LIMIT}). Values above the cap clamp silently."
                ),
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Pagination offset; rarely needed in chat.",
            },
        },
    },
}


GET_SUBSCRIPTIONS_TOOL: dict[str, Any] = {
    "name": "get_subscriptions",
    "description": (
        "Return the user's recurring subscriptions. Optionally filter by "
        "status (active, paused, cancelled). Use for questions about "
        "recurring charges, billing cadence, or upcoming charges."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {
                "type": "string",
                "enum": ["active", "paused", "cancelled"],
                "description": "Restrict to one status; omit to return all.",
            },
        },
    },
}


GET_SPENDING_SUMMARY_TOOL: dict[str, Any] = {
    "name": "get_spending_summary",
    "description": (
        "Return per-category totals for the last N calendar months "
        "(including the current month). Defaults to the current month "
        "only. Use for 'where does my money go', category breakdowns, or "
        "category-level comparisons over a window."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "months": {
                "type": "integer",
                "minimum": 1,
                "maximum": 24,
                "description": (
                    "Number of trailing calendar months to include "
                    "(default 1 = this month only)."
                ),
            },
        },
    },
}


GET_CARDS_TOOL: dict[str, Any] = {
    "name": "get_cards",
    "description": (
        "Return the user's active cards with their reward multipliers and "
        "card metadata. Use when the user asks about their cards, asks "
        "which card earns most on a category, or references a card by "
        "name without an id."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
}


# ---------------------------------------------------------------------------
# Tool implementations.
# ---------------------------------------------------------------------------


def calculate_total(user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Return the sum of transactions matching optional filters.

    Request:
        {
            "category": "Dining",
            "date_from": "2026-03-01",
            "date_to": "2026-03-31",
            "merchant_contains": "coffee",
            "amount_min": 5,
            "amount_max": 25
        }

    Response:
        {"total": "123.45", "count": 8, "truncated": false}

    Implementation note: PostgREST has no native SUM, so we fetch up to
    `RESULT_ROW_CAP + 1` matching rows and sum in Python. The +1 is what
    detects truncation without a separate COUNT query (same pattern as
    `list_transactions`'s `has_more`).
    """
    request = CalculateTotalRequest.model_validate(kwargs)
    filters = _filters_from_input(request.model_dump(exclude_none=True), allow_pagination=False)
    client = supabase_for_user(user.jwt)
    query = client.table("transactions").select("amount")
    query = apply_transaction_filters(query, filters)
    resp = query.range(0, RESULT_ROW_CAP).execute()
    rows = resp.data or []
    truncated = len(rows) > RESULT_ROW_CAP
    if truncated:
        rows = rows[:RESULT_ROW_CAP]

    # Decimal sum — `numeric` columns come back as strings from Supabase.
    total = sum((Decimal(str(row["amount"])) for row in rows), Decimal("0"))

    return CalculateTotalResponse(
        total=str(total),
        count=len(rows),
        truncated=truncated,
    ).model_dump(mode="json")


def get_transactions(user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Return transaction rows matching optional filters.

    Request:
        {"merchant_contains": "coffee", "limit": 10, "offset": 0}

    Response:
        {"items": [{"id": "...", "merchant": "Coffee Bar"}], "has_more": false}

    Delegates to `list_transactions` (Day 5 service) so HTTP + agent
    callers share one query builder. The returned shape is plain dict
    (not a pydantic model) so the loop's `json.dumps(tool_result)` step
    serializes cleanly.
    """
    request = GetTransactionsRequest.model_validate(kwargs)
    filters = _filters_from_input(request.model_dump(exclude_none=True), allow_pagination=True)
    result = list_transactions(user, filters)
    return GetTransactionsResponse(
        items=[
            _strip_keys(item.model_dump(mode="json"), ("user_id",))
            for item in result.items
        ],
        has_more=result.has_more,
    ).model_dump(mode="json")


def get_subscriptions(user: AuthedUser, *, status: str | None = None) -> dict[str, Any]:
    """Return recurring subscriptions, optionally filtered by status.

    Request:
        {"status": "active"}

    Response:
        {"items": [{"name": "Netflix", "status": "active"}], "truncated": false}
    """
    request = GetSubscriptionsRequest(status=status)
    client = supabase_for_user(user.jwt)
    query = (
        client.table("subscriptions")
        .select("*")
        .order("next_billing_date", desc=False)
    )
    if request.status is not None:
        query = query.eq("status", request.status)
    # Fetch one over the cap to detect truncation — same pattern as
    # calculate_total / list_transactions.
    resp = query.range(0, SUBSCRIPTIONS_ROW_CAP).execute()
    rows = resp.data or []
    truncated = len(rows) > SUBSCRIPTIONS_ROW_CAP
    if truncated:
        rows = rows[:SUBSCRIPTIONS_ROW_CAP]
    return GetSubscriptionsResponse(
        items=[_strip_keys(row, ("user_id",)) for row in rows],
        truncated=truncated,
    ).model_dump(mode="json")


def get_spending_summary(user: AuthedUser, *, months: int = 1) -> dict[str, Any]:
    """Return per-category totals over a trailing calendar-month window.

    Request:
        {"months": 3}

    Response:
        {
            "window_start": "2026-03-01",
            "window_months": 3,
            "breakdown": [{"category": "Dining", "total": "123.45", "count": 8}],
            "truncated": false
        }

    `months=1` is "this month so far"; `months=3` includes the current
    month plus the previous two. Anchored at the first of the start
    month so partial months don't skew totals.
    """
    request = GetSpendingSummaryRequest.model_validate({"months": months})
    months = request.months
    if months < 1:
        months = 1
    if months > 24:
        months = 24

    today = _dt.date.today()
    start = _subtract_months(_first_of_month(today), months - 1)

    client = supabase_for_user(user.jwt)
    # Upper bound at today — `/transactions/confirm` allows
    # `date.today() + 1 day` for client-side timezone slack
    # (app/routes/transactions.py:_DATE_FUTURE_SLACK), so future-dated
    # rows can legitimately exist. Without this clamp, a transaction
    # entered late at night with a TZ-shifted local midnight would
    # pollute "this month so far" — a small but trust-eroding bug,
    # since users read this number as "money already spent."
    resp = (
        client.table("transactions")
        .select("category, amount, date")
        .gte("date", start.isoformat())
        .lte("date", today.isoformat())
        .range(0, RESULT_ROW_CAP)
        .execute()
    )
    rows = resp.data or []
    truncated = len(rows) > RESULT_ROW_CAP
    if truncated:
        rows = rows[:RESULT_ROW_CAP]

    # Aggregate in Python — bounded by RESULT_ROW_CAP, no GROUP BY needed.
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for row in rows:
        cat = row["category"]
        totals[cat] = totals.get(cat, Decimal("0")) + Decimal(str(row["amount"]))
        counts[cat] = counts.get(cat, 0) + 1

    breakdown = [
        {"category": cat, "total": str(totals[cat]), "count": counts[cat]}
        for cat in sorted(totals.keys(), key=lambda c: totals[c], reverse=True)
    ]
    return GetSpendingSummaryResponse(
        window_start=start.isoformat(),
        window_months=months,
        breakdown=breakdown,
        truncated=truncated,
    ).model_dump(mode="json")


def get_cards(user: AuthedUser) -> dict[str, Any]:
    """Return active cards available to the agent.

    Request:
        {}

    Response:
        {"items": [{"id": "...", "name": "Amex Gold", "active": true}]}
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("cards")
        .select("*")
        .eq("active", True)
        .order("created_at", desc=False)
        .execute()
    )
    rows = resp.data or []
    return GetCardsResponse(
        items=[_strip_keys(row, ("user_id",)) for row in rows],
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Registry.
# ---------------------------------------------------------------------------


# Tool name → (schema, executor). The loop iterates this dict to build the
# `tools=` argument and to dispatch `tool_use` blocks. Pairing schemas with
# executors here means a tool that ships without one or the other can't
# slip through.
TOOL_REGISTRY: dict[str, tuple[dict[str, Any], Callable[..., Any]]] = {
    CALCULATE_TOTAL_TOOL["name"]: (CALCULATE_TOTAL_TOOL, calculate_total),
    GET_TRANSACTIONS_TOOL["name"]: (GET_TRANSACTIONS_TOOL, get_transactions),
    GET_SUBSCRIPTIONS_TOOL["name"]: (GET_SUBSCRIPTIONS_TOOL, get_subscriptions),
    GET_SPENDING_SUMMARY_TOOL["name"]: (GET_SPENDING_SUMMARY_TOOL, get_spending_summary),
    GET_CARDS_TOOL["name"]: (GET_CARDS_TOOL, get_cards),
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
    _schema, executor = TOOL_REGISTRY[name]
    return executor(user, **tool_input)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _first_of_month(d: _dt.date) -> _dt.date:
    """Return the first day of the month containing `d`."""
    return d.replace(day=1)


def _subtract_months(d: _dt.date, months: int) -> _dt.date:
    """Subtract `months` calendar months, anchored at day=1.

    Pure-stdlib; avoids dragging in dateutil for one call site.
    """
    total = d.year * 12 + (d.month - 1) - months
    year, month = divmod(total, 12)
    return _dt.date(year, month + 1, 1)


def _filters_from_input(payload: dict[str, Any], *, allow_pagination: bool) -> TransactionFilters:
    """Build `TransactionFilters` from validated tool input.

    Pydantic does the type coercion (strings to date / Decimal). When the
    tool doesn't accept pagination (`calculate_total`), strip `limit`
    and `offset` defensively so a hallucinated field can't push us into
    a different code path.
    """
    if not allow_pagination:
        payload = {k: v for k, v in payload.items() if k not in {"limit", "offset"}}
    return TransactionFilters.model_validate(payload)


def _strip_keys(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Drop redundant keys from a tool response.

    RLS already scopes by `user_id`, so emitting it on every row just
    burns tokens. Same for any per-row metadata Claude won't reason
    about.
    """
    return {k: v for k, v in row.items() if k not in keys}
