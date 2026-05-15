"""Typed tools the Claude Haiku agent can call.

DESIGN.md §7.2 mandates typed tools — no `run_query(sql)` surface, no SQL
generation. Each tool is a Python function callable with the user's JWT in
scope so RLS scopes the read automatically.

Day 9a ships the read surface (`get_transactions`, `calculate_total`,
`get_subscriptions`, `get_spending_summary`, `get_cards`). All five share
the `TransactionFilters` filter shape where they overlap, routed through
`apply_transaction_filters` in `app/services/transactions.py` so the
HTTP-endpoint filter list and the agent-tool filter list cannot drift.

Day 9b adds `propose_transaction` (returns a `TransactionProposal`
without writing — confirm-then-commit per CLAUDE.md invariant 8) and
`set_goal` (the lone direct-write carve-out for low-risk reversible rows
per DESIGN.md §7.2). Day 14 / Day 19 add `propose_card` /
`propose_subscription`. Each of those days extends `TOOL_REGISTRY` —
the registry is the only seam Claude sees, so a tool that isn't
registered there is invisible to the model.

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
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.integrations.gemini import GeminiError, categorize
from app.models.goals import Goal, GoalPeriod, SetGoalRequest
from app.models.transactions import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TransactionFilters,
    TransactionProposal,
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


class ProposeTransactionRequest(BaseModel):
    """Tool input for `propose_transaction`.

    `card_id` is UUID-only — Claude resolves card names via `get_cards`
    first (already in its tool list from Day 9a) and passes the UUID.
    Rationale: keeping the input tightly typed lets the agent loop reason
    about ambiguity (two cards both nicknamed "Amex") by asking a
    clarifying question in chat, rather than having the tool silently
    pick one.

    `category` is optional — when omitted, the tool calls Gemini to fill
    it. When supplied (Claude pre-fills from explicit user text like
    "spent $7 on coffee at Blue Bottle"), the tool accepts it without
    re-categorizing — there's no Gemini baseline to learn against in
    that branch and that is the correct semantic.

    Example request:
        {"merchant": "Trader Joe's", "amount": 47, "date": "2026-05-13",
         "card_id": "f1e2d3c4-..."}
    """

    model_config = ConfigDict(extra="forbid")

    merchant: str
    amount: Decimal
    date: _dt.date
    card_id: UUID | None = None
    category: str | None = None
    notes: str | None = None

    @field_validator("merchant")
    @classmethod
    def _v_merchant(cls, value: str) -> str:
        """Strip leading/trailing whitespace and reject empty merchant names."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("merchant cannot be empty or whitespace-only")
        return stripped

    @field_validator("category")
    @classmethod
    def _v_category(cls, value: str | None) -> str | None:
        """Reject pre-filled categories outside Tameru's closed enum."""
        if value is not None and value not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category {value!r} is not in the closed enum (see app/prompts/categories.py)"
            )
        return value

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, value: Decimal) -> Decimal:
        """Reject non-positive transaction amounts at the model layer."""
        if value <= 0:
            raise ValueError(f"amount must be > 0 (got {value})")
        return value


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


class ChartSeries(BaseModel):
    """One labeled series in a render_chart spec.

    Length contract: `data` length must equal `len(x)` on the parent
    `RenderChartRequest`. Pydantic can't express that cross-field rule
    statically, so we enforce it on the parent validator below.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    data: list[float]


class RenderChartRequest(BaseModel):
    """Tool input for `render_chart`.

    Example request:
        {
            "type": "line",
            "x": ["Mar W1", "Mar W2"],
            "series": [{"name": "Dining", "data": [142.0, 211.5]}],
            "y_label": "USD",
            "title": "dining by week, march"
        }

    `series` must be non-empty, each `data` array must match `len(x)`,
    and `donut` charts take exactly one series.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^(line|bar|stacked_bar|donut)$")
    x: list[str] = Field(min_length=1)
    series: list[ChartSeries] = Field(min_length=1)
    y_label: str | None = None
    title: str

    @field_validator("series")
    @classmethod
    def _series_data_lengths(
        cls, value: list[ChartSeries], info: Any  # noqa: ARG003 — used by side
    ) -> list[ChartSeries]:
        """All series must agree on length; donut takes exactly one."""
        # Pydantic v2 sneaks the model values in via `info.data` once the
        # x field has already validated. If x failed earlier validation,
        # this validator is skipped — no need to defend against missing x.
        x = info.data.get("x")
        if not isinstance(x, list):
            return value
        for s in value:
            if len(s.data) != len(x):
                raise ValueError(
                    f"series {s.name!r} has {len(s.data)} points but x has {len(x)}"
                )
        chart_type = info.data.get("type")
        if chart_type == "donut" and len(value) != 1:
            raise ValueError("donut charts take exactly one series")
        return value


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


PROPOSE_TRANSACTION_TOOL: dict[str, Any] = {
    "name": "propose_transaction",
    "description": (
        "Build a transaction proposal from a user-described purchase. "
        "Returns a TransactionProposal — it does NOT write a row. The "
        "client renders the proposal as a parse card; the row is only "
        "created when the user taps the confirm button (which triggers "
        "POST /transactions/confirm). If the user names a card, call "
        "get_cards first to resolve the UUID and pass it as card_id. "
        "Do not say 'I added it' after calling this tool — the row does "
        "not exist yet."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["merchant", "amount", "date"],
        "properties": {
            "merchant": {
                "type": "string",
                "description": "Merchant name as the user wrote it.",
            },
            "amount": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Transaction amount in the user's home currency.",
            },
            "date": {
                "type": "string",
                "format": "date",
                "description": "Transaction date (YYYY-MM-DD).",
            },
            "card_id": {
                "type": "string",
                "format": "uuid",
                "description": (
                    "Card UUID resolved from get_cards. Do not pass a "
                    "card name; resolve it via get_cards first."
                ),
            },
            "category": {
                "type": "string",
                "enum": list(ALLOWED_CATEGORIES),
                "description": (
                    "Optional — fill only when the user explicitly named "
                    "a category. Omit otherwise; the tool will call "
                    "Gemini for a suggestion."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Free-form notes the user mentioned.",
            },
        },
    },
}


RENDER_CHART_TOOL: dict[str, Any] = {
    "name": "render_chart",
    "description": (
        "Render a chart in the chat thread. Use for any question whose "
        "answer is shaped like a comparison or trend over time — 'chart my "
        "dining by week in March', 'compare groceries vs dining last month', "
        "'how has subscriptions changed'. ALWAYS extract the numbers first "
        "with calculate_total / get_spending_summary, then feed them in as "
        "the `series` array. This tool is pure presentation — it does NOT "
        "query the database; it echoes the spec back so the frontend can "
        "draw it (DESIGN.md §7.8). One render_chart call per turn; if the "
        "user asks for multiple unrelated charts, prefer to answer in prose."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "x", "series", "title"],
        "properties": {
            "type": {
                "type": "string",
                "enum": ["line", "bar", "stacked_bar", "donut"],
                "description": (
                    "line for trends over time, bar/stacked_bar for category "
                    "comparisons, donut for share-of-total breakdowns."
                ),
            },
            "x": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "X-axis labels (e.g. ['Mar W1', 'Mar W2', ...] or "
                    "['Groceries', 'Dining', ...]). For donut charts this "
                    "is the slice labels."
                ),
            },
            "series": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "data"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Series label shown in the legend. For "
                                "donut charts, use a single series."
                            ),
                        },
                        "data": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "One value per x-axis label. Length MUST "
                                "equal len(x)."
                            ),
                        },
                    },
                },
                "description": (
                    "One series for line/bar/donut, two or more for "
                    "stacked_bar. Use home-currency dollars (not cents)."
                ),
            },
            "y_label": {
                "type": "string",
                "description": (
                    "Y-axis label, e.g. 'USD' or 'transactions'. Omit for "
                    "donut charts."
                ),
            },
            "title": {
                "type": "string",
                "description": (
                    "Short chart title, ~6 words. Lowercase preferred to "
                    "match the app's voice."
                ),
            },
        },
    },
}


SET_GOAL_TOOL: dict[str, Any] = {
    "name": "set_goal",
    "description": (
        "Set a spending budget for a category and period. SETS — does not "
        "ADD. Calling set_goal(category='Dining', amount=300, period='month') "
        "after a prior $400/month goal replaces the existing goal in place. "
        "Omit category to set an overall budget across all categories."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount", "period"],
        "properties": {
            "category": {
                "type": "string",
                "enum": list(ALLOWED_CATEGORIES),
                "description": "Closed-enum category; omit for an overall budget.",
            },
            "amount": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Budget amount in the user's home currency.",
            },
            "period": {
                "type": "string",
                "enum": ["week", "month", "year"],
                "description": "Budget window.",
            },
        },
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


def propose_transaction(user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Build a TransactionProposal from a chat-described purchase.

    Request:
        {
            "merchant": "Trader Joe's",
            "amount": 47,
            "date": "2026-05-13",
            "card_id": "f1e2d3c4-..."   # optional, UUID-only
            "category": "Groceries",     # optional; omit to let Gemini fill
            "notes": "weekly run"        # optional
        }

    Response:
        TransactionProposal-shaped dict; see app/models/transactions.py.

    The tool does NOT write to `transactions`. The structural test in
    tests/contracts/test_tool_write_invariant.py enforces this — if a
    refactor here ever calls `.insert(` / `.upsert(` / `.update(` /
    `.delete(` / `.rpc(`, the test fails.

    Behavior contract:
      * `category` supplied by Claude → accepted as-is, `gemini_suggestion`
        stays None. No Gemini baseline to learn against in this branch.
      * `category` omitted → call categorize(merchant, user). On success,
        set both `category` and `gemini_suggestion` to the Gemini result;
        the two fields start equal and diverge only when the user edits
        on the parse card. That divergence is the training signal the
        confirm endpoint's merchant_category upsert consumes
        (app/routes/transactions.py:97-101). Without `gemini_suggestion`
        carrying Gemini's frozen guess, the learning loop never fires.
      * `categorize()` raises GeminiError → fall back to
        category="Other", gemini_suggestion=None. The categorize call
        already wrote its own ai_call_log row before raising.
      * `card_id` supplied → verify it's the user's via an RLS-scoped
        cards SELECT. Hallucinated UUIDs, deleted cards, and cross-user
        UUIDs all look identical to the lookup (the latter two via RLS
        returning empty). In all three cases, drop card_id to None so the
        parse card prompts the user to pick rather than failing at
        commit time on `_assert_card_owned`.
    """
    request = ProposeTransactionRequest.model_validate(kwargs)

    if request.category is not None:
        category = request.category
        gemini_suggestion: str | None = None
    else:
        try:
            suggestion = categorize(request.merchant, user)
            category = suggestion.category
            gemini_suggestion = suggestion.category
        except GeminiError:
            category = "Other"
            gemini_suggestion = None

    client = supabase_for_user(user.jwt)
    card_id = request.card_id
    if card_id is not None and not _card_belongs_to_user(client, card_id):
        card_id = None

    # Carry the merchant through in its display form (validator already
    # stripped leading/trailing whitespace). The §8.2 schema says
    # transactions.merchant is "as entered or parsed" — the case-preserving
    # value the user sees in their ledger. normalize_merchant() lowercases
    # for the merchant_category JOIN key (§8.4), which is a different
    # column with a different purpose; the confirm route handles that
    # normalization itself when it upserts the past-correction row
    # (app/routes/transactions.py:_upsert_merchant_correction), and
    # categorize() normalizes internally for its own cache lookup. Pre-
    # normalizing here would defeat Day 9c's canonicalization win — the
    # whole point is that Claude picks "Kentucky Fried Chicken" from the
    # top_user_merchants block, and the user should see that exact form
    # on the parse card, not "kentucky fried chicken".
    proposal = TransactionProposal(
        merchant=request.merchant,
        amount=request.amount,
        date=request.date,
        card_id=card_id,
        category=category,
        notes=request.notes,
        gemini_suggestion=gemini_suggestion,
        client_request_id=uuid4(),
    )
    return proposal.model_dump(mode="json")


def set_goal(user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Upsert a spending budget for a (category, period) slot.

    Request:
        {"category": "Dining", "amount": 300, "period": "month"}

    Response:
        Goal-shaped dict; see app/models/goals.py.

    Latest-wins is enforced at the schema layer by the
    `goals_user_cat_period_uniq` constraint + this PostgREST upsert. The
    `NULLS NOT DISTINCT` modifier (Postgres 15+) folds NULL-category rows
    into one bucket so an overall-budget set is also idempotent. This is
    the lone direct-write tool — see CLAUDE.md invariant 8 and the
    structural test at tests/contracts/test_tool_write_invariant.py.
    """
    request = SetGoalRequest.model_validate(kwargs)
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("goals")
        .upsert(
            {
                "user_id": str(user.user_id),
                "category": request.category,
                "amount": str(request.amount),
                "period": request.period,
            },
            on_conflict="user_id,category,period",
        )
        .execute()
    )
    return Goal.model_validate(resp.data[0]).model_dump(mode="json")


def render_chart(_user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Echo a chart spec for the frontend to render.

    Request:
        {
            "type": "line",
            "x": ["Mar W1", "Mar W2", "Mar W3", "Mar W4"],
            "series": [{"name": "Dining", "data": [142.0, 211.5, 180.0, 175.0]}],
            "y_label": "USD",
            "title": "dining by week, march"
        }

    Response:
        Same shape, verbatim. The agent loop ships it to the frontend
        inside a tool_call block; the frontend renders it via
        components/chat/Chart.tsx.

    No DB read, no DB write — `render_chart` is purely a transport for
    "the model decided to chart this." Data extraction belongs upstream
    in calculate_total / get_spending_summary; mixing it into this tool
    would couple presentation to query logic and make eval traces harder
    to read. The structural test in tests/contracts/test_tool_write_invariant.py
    is satisfied by the no-DB body — keep it that way on future edits.

    The `_user` parameter exists because every executor signs the same
    `(user, **kwargs)` contract — `execute_tool` passes it positionally.
    render_chart legitimately doesn't read it; underscore signals intent
    to readers and quiets static analyzers without dropping the slot.
    """
    return RenderChartRequest.model_validate(kwargs).model_dump(mode="json")


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
    PROPOSE_TRANSACTION_TOOL["name"]: (PROPOSE_TRANSACTION_TOOL, propose_transaction),
    SET_GOAL_TOOL["name"]: (SET_GOAL_TOOL, set_goal),
    RENDER_CHART_TOOL["name"]: (RENDER_CHART_TOOL, render_chart),
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


def _card_belongs_to_user(client: Any, card_id: UUID) -> bool:
    """Defensive check used by `propose_transaction`.

    Returns True iff the RLS-scoped client sees the card AND the card is
    still `active=true`. Hallucinated UUIDs, cross-user UUIDs, and
    soft-deleted cards (`active=false`, see DESIGN.md §8.1) all return
    False. The `active` filter matters because `get_cards` only returns
    active cards — but stale conversation history can still surface an
    inactive card's UUID to Claude, and we don't want a chat-typed
    transaction to be silently posted against a card the user closed.
    Confirm-side `_assert_card_owned` does not (yet) filter on `active`;
    flagging that gap for a follow-up so propose and confirm don't drift.
    """
    resp = (
        client.table("cards")
        .select("id")
        .eq("id", str(card_id))
        .eq("active", True)
        .limit(1)
        .execute()
    )
    return bool(resp.data)
