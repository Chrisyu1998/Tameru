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
from app.integrations.card_lookup import lookup_card
from app.integrations.card_regions import region_for_currency
from app.models.cards import (
    CardIssuer,
    CardLookupResult,
    CardNetwork,
    CardProgram,
    CardProposal,
    CardRegion,
)
from app.models.goals import Goal, GoalPeriod, SetGoalRequest
from app.models.subscriptions import (
    Frequency,
    SubscriptionProposal,
    compute_next_billing_date,
)
from app.models.transactions import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TransactionFilters,
)
from app.prompts.categories import ALLOWED_CATEGORIES
from app.services.transactions import (
    apply_transaction_filters,
    build_transaction_proposal,
    list_transactions,
)
from app.util.timezone import user_local_today

# Hard cap on rows fetched for an aggregation. Above this we still return
# the partial sum but flag the result as truncated so Claude can ask the
# user to narrow filters. Sized for v1 scale — a single user crossing 5k
# transactions in a single filter window is a future-Tameru problem.
RESULT_ROW_CAP = 5_000

# PostgREST silently caps every response at `max-rows` (1000 on Supabase —
# memory.md 2026-05-26), so aggregation reads must page at or below that
# size; a single `.range(0, RESULT_ROW_CAP)` request would come back with
# at most 1000 rows and no error, silently under-counting totals.
AGGREGATION_PAGE_SIZE = 1_000

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
    card_id: str | None = Field(
        default=None,
        description="Card UUID filter — direct/HTTP callers only; not in the model-facing schema.",
    )
    card_ref: str | None = Field(
        default=None,
        description="Short card handle from get_cards (e.g. 'amex-1001') — the agent's path.",
    )
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

    Two windowing modes, mutually exclusive in practice:

      * trailing window — `{"months": 3}` covers the current month plus
        the previous two. This is the "where does my money go lately"
        shape.
      * explicit window — `{"date_from": "2026-03-01", "date_to":
        "2026-03-31"}` covers exactly that range. This is the shape for
        a *specific named month* ("breakdown for March") — the trailing
        window cannot express it. When either date is supplied, the
        explicit window wins and `months` is ignored.
    """

    model_config = ConfigDict(extra="forbid")

    months: int = Field(default=1)
    date_from: _dt.date | None = Field(
        default=None,
        description="Inclusive window start; pair with date_to for a specific period.",
    )
    date_to: _dt.date | None = Field(
        default=None,
        description="Inclusive window end; defaults to today when only date_from is set.",
    )


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
            "window_end": "2026-03-31",
            "window_months": 1,
            "breakdown": [{"category": "Dining", "total": "123.45", "count": 8}],
            "truncated": false
        }

    `window_start` / `window_end` are the actual inclusive bounds the
    totals cover — the agent should read them back to confirm it asked
    for the period it meant. `window_months` is the count of calendar
    months the window spans.
    """

    window_start: str
    window_end: str
    window_months: int
    breakdown: list[SpendingSummaryRow]
    truncated: bool


class GetCardsResponse(BaseModel):
    """Tool result for `get_cards`.

    Example response:
        {"items": [{"id": "...", "name": "Amex Gold", "status": "active"}]}
    """

    items: list[dict[str, Any]]


class ProposeTransactionRequest(BaseModel):
    """Tool input for `propose_transaction`.

    `card_ref` is the short card handle (`{issuer}-{last_four}`, e.g.
    "amex-1001") emitted by `get_cards`. The agent passes this rather
    than the card's UUID: copying a 36-char random UUID between tool
    calls is error-prone for an LLM (a dropped hex digit silently loses
    the card attribution — observed in the Day 22 eval), whereas the
    short handle is meaningful and a slip simply fails to resolve rather
    than mis-resolving. `card_id` is retained as a UUID-typed field for
    direct (non-agent) callers and tests; the agent-facing JSON schema
    exposes only `card_ref`.

    `category` is optional — when omitted, the tool calls Gemini to fill
    it. When supplied (Claude pre-fills from explicit user text like
    "spent $7 on coffee at Blue Bottle"), the tool accepts it without
    re-categorizing — there's no Gemini baseline to learn against in
    that branch and that is the correct semantic.

    `date` is optional. When the user gives no date, the agent OMITS it
    and the tool fills the user's local `today` (`user_local_today`,
    resolved under RLS in `users_meta.timezone`) server-side — the date
    on the pure default path is never routed through the model. LLMs are
    unreliable at copying/computing a date (the same transcription-error
    class that forced the `card_ref` short handle), and the injected
    "Today is …" anchor is only as correct as the model's reading of it.
    The agent still supplies `date` for explicit/relative dates
    ("yesterday", "last Friday"), which it must compute from the anchor.

    Example request:
        {"merchant": "Trader Joe's", "amount": 47, "date": "2026-05-13",
         "card_ref": "amex-1001"}
    """

    model_config = ConfigDict(extra="forbid")

    merchant: str
    amount: Decimal
    date: _dt.date | None = None
    card_id: UUID | None = None
    card_ref: str | None = None
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
    # card_ref, NOT card_id: the model-facing filter takes the short
    # get_cards handle. A raw UUID here would put a 36-char random string
    # back on the model's copy path — the exact transcription failure
    # chat_v10 eliminated for the propose_* tools (a slipped hex digit
    # keeps valid UUID format, matches nothing under RLS, and the tool
    # would confidently report $0). The Pydantic request models still
    # accept card_id for direct/HTTP callers.
    "card_ref": {
        "type": "string",
        "description": (
            "Restrict to one card by its short ref handle from get_cards "
            "(e.g. 'amex-1001'). Copy the ref exactly; never pass a UUID."
        ),
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
        "Return per-category spending totals over a window. Use for "
        "'where does my money go', category breakdowns, or category-level "
        "comparisons. Two windowing modes: pass `months` for a trailing "
        "window ending today (months=1 = this month so far, months=3 = "
        "this month plus the previous two); OR pass `date_from` and "
        "`date_to` for an explicit range. For a SPECIFIC named month or "
        "past period ('breakdown for March', 'spending between Jan and "
        "Mar'), you MUST pass date_from/date_to — the trailing `months` "
        "window cannot isolate a single past month. Today's date is in "
        "the system prompt; compute the range from it."
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
                    "(default 1 = this month only). Ignored when "
                    "date_from/date_to are supplied."
                ),
            },
            "date_from": {
                "type": "string",
                "format": "date",
                "description": (
                    "Inclusive window start (YYYY-MM-DD). Pair with "
                    "date_to to target a specific month or period."
                ),
            },
            "date_to": {
                "type": "string",
                "format": "date",
                "description": (
                    "Inclusive window end (YYYY-MM-DD). Defaults to today "
                    "when only date_from is given."
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
        "name. Each card includes a short `ref` handle (e.g. "
        "'amex-1001') — pass that `ref` to propose_transaction / "
        "propose_subscription when the user names a card, and to "
        "calculate_total / get_transactions' `card_ref` filter for "
        "per-card spend questions. Copy the ref exactly; UUIDs are never "
        "used between tools."
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
        "get_cards first and pass the matching card's short `ref` handle "
        "(e.g. 'amex-1001') as card_ref. "
        "Do not say 'I added it' after calling this tool — the row does "
        "not exist yet."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["merchant", "amount"],
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
                "description": (
                    "Transaction date (YYYY-MM-DD). OMIT this field when the "
                    "user does not say when the purchase happened — the server "
                    "fills today's date in the user's timezone. Only set it for "
                    "an explicit or relative date the user gives ('yesterday', "
                    "'last Friday', 'on the 3rd'), computed from today's date "
                    "in this prompt. Never guess today's date yourself for the "
                    "no-date case."
                ),
            },
            "card_ref": {
                "type": "string",
                "description": (
                    "The card's short `ref` handle from get_cards "
                    "(e.g. 'amex-1001'). When the user names a card, call "
                    "get_cards, find the matching card, and pass its "
                    "`ref` here. Do NOT pass the card's long `id` UUID — "
                    "copy the short ref. Omit when no card is named."
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


PROPOSE_CARD_TOOL: dict[str, Any] = {
    "name": "propose_card",
    "description": (
        "Build a card proposal from a user-described credit card the user "
        "wants to add to their wallet. The tool runs a web_search-backed "
        "lookup against authoritative card-rewards sources (NerdWallet, "
        "The Points Guy, US Credit Card Guide, Doctor of Credit) to fill "
        "in the rewards program, issuer, category multipliers, annual fee, "
        "the card network, and citations. Returns a CardProposal — it does "
        "NOT write a row. The client renders the proposal as a parse card; "
        "the row is only created when the user taps the confirm button "
        "(which triggers POST /cards/confirm). "
        "Only `program` (the card's name) is required. The lookup fills "
        "issuer and network from the card name — DO NOT ask the user "
        "which bank issued their card or which network it's on. Pass "
        "`network` only if the user explicitly named it ('my Visa "
        "Sapphire'). Pass `last_four` if the user said it ('ending 4321'); "
        "otherwise omit and the parse-card UI will collect it before "
        "the user confirms. "
        "Pass `region` ('US'/'JP'/'TW') when the card clearly belongs to a "
        "region — infer it from the issuer or card name (Chase, Amex, Citi, "
        "Capital One, Bilt -> US; Rakuten, JCB, SMBC, AEON, Epos, Saison -> "
        "JP; Cathay, E.SUN, CTBC, Taishin, Fubon -> TW). This routes the "
        "lookup to the right sources and reward model; omit it only when the "
        "card's region is genuinely unclear (it then defaults to the user's "
        "home region). "
        "Do not say 'I added it' after calling this "
        "tool — the row does not exist yet."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["program"],
        "properties": {
            "region": {
                "type": "string",
                # Keep in sync with `CardRegion` in app/models/cards.py.
                "enum": ["US", "JP", "TW"],
                "description": (
                    "Card region — which country's sources + reward model "
                    "the lookup uses. Infer from the issuer/card name "
                    "(US banks -> US; Rakuten/JCB/SMBC/etc. -> JP; "
                    "Cathay/E.SUN/etc. -> TW). OPTIONAL — omit when unclear "
                    "and it defaults to the user's home region. Crucial for "
                    "the mixed-wallet case (e.g. a Japan-based user adding a "
                    "US card): without it the lookup wrongly uses the home "
                    "region."
                ),
            },
            "network": {
                "type": "string",
                # Keep in sync with `CardNetwork` in app/models/cards.py +
                # the `cards_network_check` CHECK. Tier 3 added jcb/diners.
                "enum": [
                    "visa",
                    "mastercard",
                    "amex",
                    "discover",
                    "jcb",
                    "diners",
                    "other",
                ],
                "description": (
                    "Card network. OPTIONAL. Only pass when the user "
                    "explicitly named it ('my Visa Sapphire'). For nearly "
                    "every product the lookup fills it from the card name "
                    "— do NOT ask the user."
                ),
            },
            "last_four": {
                "type": "string",
                "pattern": r"^\d{4}$",
                "description": (
                    "Last 4 digits of the card number. OPTIONAL. Pass it "
                    "if the user said it ('ending 4321'); otherwise omit "
                    "and let the parse-card UI surface an input. The user "
                    "knows this — don't pre-emptively block the proposal "
                    "to ask for it."
                ),
            },
            "program": {
                "type": "string",
                "description": (
                    "The card's display name as the user said it, e.g. "
                    "'Chase Sapphire Reserve', 'Amex Gold'. This becomes "
                    "the `name` on the proposed card and is also what we "
                    "search the web for. NOT the rewards-program enum — "
                    "the lookup fills that in."
                ),
            },
            "alias": {
                "type": "string",
                "description": (
                    "Optional nickname the user wants to remember the card "
                    "by (e.g. 'travel card'). Not currently stored as a "
                    "separate column — included for forward-compat."
                ),
            },
            "next_annual_fee_date": {
                "type": "string",
                "format": "date",
                "description": (
                    "OPTIONAL. Next renewal date for the card's annual fee, "
                    "YYYY-MM-DD. Pass ONLY when the user mentioned when "
                    "the AF hits ('renews in March', 'my AF is March 15'). "
                    "Do NOT guess — the date is per-user and the web "
                    "doesn't know it. When set alongside a non-zero "
                    "annual_fee, the confirm endpoint creates a companion "
                    "subscription so the auto-logger logs the AF on each "
                    "anniversary."
                ),
            },
        },
    },
}


PROPOSE_SUBSCRIPTION_TOOL: dict[str, Any] = {
    "name": "propose_subscription",
    "description": (
        "Build a recurring-subscription proposal from a user-described "
        "recurring charge. Use when the user wants to TRACK a recurring "
        "bill — Netflix monthly, rent monthly, gym, software subscriptions. "
        "Returns a SubscriptionProposal — it does NOT write a row. The "
        "client renders the proposal as a parse card; the row is only "
        "created when the user taps the confirm button (which triggers "
        "POST /subscriptions/confirm). "
        "If the user names a card ('on my Amex Gold'), call get_cards "
        "first and pass the matching card's short `ref` handle (e.g. "
        "'amex-1001') as card_ref. If the user doesn't name a card "
        "(e.g. 'track my rent' — usually bank ACH), OMIT card_ref; the "
        "subscription saves as cardless and pg_cron auto-logs cardless "
        "transactions. "
        "Do not say 'I added it' after calling this tool — the row does "
        "not exist yet. Mention that future auto-logged charges will "
        "show up in the ledger; the first charge is NOT backfilled — "
        "the user logs today's charge manually if they want it captured."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "amount", "frequency", "start_date", "category"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Subscription name as the user wrote it, e.g. 'Netflix', "
                    "'Rent', 'Spotify family'."
                ),
            },
            "amount": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Recurring amount per period, in home currency.",
            },
            "frequency": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly", "annual"],
                "description": (
                    "Billing cadence. Most subscriptions are monthly; annual "
                    "is common for software and AmazonPrime; quarterly and "
                    "weekly are rare."
                ),
            },
            "start_date": {
                "type": "string",
                "format": "date",
                "description": (
                    "When the subscription started (YYYY-MM-DD). Used by the "
                    "forward-only rule to compute the next billing date: if "
                    "start_date is today or in the past, the first auto-log "
                    "is start_date + 1 period; past cycles are NOT backfilled."
                ),
            },
            "category": {
                "type": "string",
                "enum": list(ALLOWED_CATEGORIES),
                "description": (
                    "Closed-enum category. 'Streaming' for Netflix/Spotify/"
                    "Apple Music/YouTube Premium/Disney+ (media specifically); "
                    "'Memberships' for non-streaming recurring (software, "
                    "gym, news, Patreon, cloud storage); 'Home' for "
                    "rent/mortgage/HOA; 'Utilities' for phone/internet/"
                    "electric. If unclear, ask the user."
                ),
            },
            "card_ref": {
                "type": "string",
                "description": (
                    "OPTIONAL. The card's short `ref` handle from "
                    "get_cards (e.g. 'amex-1001'). Pass when the user "
                    "named a card — do NOT pass the long `id` UUID. OMIT "
                    "for bank-ACH bills (rent, utilities, mortgage) — the "
                    "subscription saves as cardless and pg_cron auto-logs "
                    "with no card attribution."
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

    Implementation note: PostgREST has no native SUM, so we page through
    matching rows (≤1000/page — PostgREST's `max-rows` cap) up to
    `RESULT_ROW_CAP` and sum in Python. Truncation is detected by paging
    one row past the cap (same pattern as `goals._sum_active_transactions`).
    """
    request = CalculateTotalRequest.model_validate(kwargs)
    client = supabase_for_user(user.jwt)
    payload = _resolve_card_ref_filter(client, request.model_dump(exclude_none=True))
    filters = _filters_from_input(payload, allow_pagination=False)

    def build_query() -> Any:
        """Build a fresh filtered query per page — `.range()` must be
        applied to a fresh builder each time (goals.py precedent)."""
        # Reads through `active_transactions` so soft-deleted rows don't
        # appear in agent totals (DESIGN.md §8 status-column doctrine).
        query = client.table("active_transactions").select("amount")
        return apply_transaction_filters(query, filters)

    rows, truncated = _fetch_aggregation_pages(build_query, cap=RESULT_ROW_CAP)

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
    client = supabase_for_user(user.jwt)
    payload = _resolve_card_ref_filter(client, request.model_dump(exclude_none=True))
    filters = _filters_from_input(payload, allow_pagination=True)
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

    # Resolve card_id → the short get_cards ref server-side. Answering
    # "which card pays for Netflix?" used to require the model to
    # visually cross-match two 36-char UUIDs between this result and
    # get_cards — same failure family as the chat_v10 transcription bug,
    # with no fail-closed backstop (a mismatch is just a wrong prose
    # answer). The raw ids (subscription id, card_id, client_request_id)
    # are stripped: no agent tool consumes them (audit P3-35).
    card_refs = _card_refs_by_id(client)
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _strip_keys(row, ("user_id", "id", "card_id", "client_request_id"))
        card_id = row.get("card_id")
        item["card_ref"] = card_refs.get(card_id) if card_id else None
        items.append(item)
    return GetSubscriptionsResponse(
        items=items,
        truncated=truncated,
    ).model_dump(mode="json")


def get_spending_summary(
    user: AuthedUser,
    *,
    months: int = 1,
    date_from: Any = None,
    date_to: Any = None,
) -> dict[str, Any]:
    """Return per-category totals over a calendar window.

    Request (trailing window):
        {"months": 3}

    Request (explicit window — a specific named month):
        {"date_from": "2026-03-01", "date_to": "2026-03-31"}

    Response:
        {
            "window_start": "2026-03-01",
            "window_end": "2026-03-31",
            "window_months": 1,
            "breakdown": [{"category": "Dining", "total": "123.45", "count": 8}],
            "truncated": false
        }

    Two windowing modes:
      * trailing — `months=1` is "this month so far"; `months=3` is the
        current month plus the previous two, anchored at the first of the
        start month so partial months don't skew totals.
      * explicit — when `date_from` and/or `date_to` are supplied, the
        window is exactly that range and `months` is ignored. This is the
        only mode that can answer "breakdown for March" when today is in
        a later month; the trailing window cannot isolate a single past
        month.
    """
    request = GetSpendingSummaryRequest.model_validate(
        {"months": months, "date_from": date_from, "date_to": date_to}
    )

    # User-local "today" (users_meta.timezone, UTC fallback). A server-UTC
    # anchor excludes east-of-UTC users' local-today rows from "this month
    # so far" every local morning — the end-clamp below would cut them off
    # (audit P2-7).
    today = user_local_today(user.jwt)
    if request.date_from is not None or request.date_to is not None:
        # Explicit window. A lone date_from runs through today; a lone
        # date_to runs from the first of its month. Both supplied → the
        # exact range.
        start = request.date_from or _first_of_month(request.date_to)  # type: ignore[arg-type]
        end = request.date_to or today
        window_months = _months_spanned(start, end)
    else:
        # Trailing window — same clamp + anchor as before.
        m = max(1, min(24, request.months))
        start = _subtract_months(_first_of_month(today), m - 1)
        # Upper bound at today — `/transactions/confirm` allows
        # `date.today() + 1 day` for client-side timezone slack, so
        # future-dated rows can legitimately exist; without this clamp a
        # TZ-shifted late-night entry would pollute "this month so far."
        end = today
        window_months = m

    client = supabase_for_user(user.jwt)

    def build_query() -> Any:
        """Build a fresh windowed query per page — see calculate_total."""
        # Default-safe read via the view (DESIGN.md §8).
        return (
            client.table("active_transactions")
            .select("category, amount, date")
            .gte("date", start.isoformat())
            .lte("date", end.isoformat())
        )

    rows, truncated = _fetch_aggregation_pages(build_query, cap=RESULT_ROW_CAP)

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
        window_end=end.isoformat(),
        window_months=window_months,
        breakdown=breakdown,
        truncated=truncated,
    ).model_dump(mode="json")


def get_cards(user: AuthedUser) -> dict[str, Any]:
    """Return active cards available to the agent.

    Request:
        {}

    Response:
        {"items": [{"ref": "amex-1001", "name": "Amex Gold",
                    "status": "active"}]}

    Each item carries a short `ref` handle (`{issuer}-{last_four}`). The
    agent passes `ref` — not a UUID — to propose_transaction /
    propose_subscription's `card_ref` arg and to the read tools'
    `card_ref` filter. UUIDs are error-prone for an LLM to copy verbatim
    between tool calls; the short handle is robust and a slip fails
    closed rather than mis-resolving. The row's `id` (and
    `client_request_id`) are deliberately NOT in the response — nothing
    in the agent path consumes them, so a long random id in context is
    pure transcription temptation + token cost (audit P3-35).
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("cards")
        .select("*")
        .eq("status", "active")
        .order("created_at", desc=False)
        .execute()
    )
    rows = resp.data or []
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _strip_keys(row, ("user_id", "id", "client_request_id"))
        item["ref"] = _card_ref(row.get("issuer"), row.get("last_four"), row.get("id"))
        items.append(item)
    return GetCardsResponse(items=items).model_dump(mode="json")


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
      * `date` omitted (user gave no date) → filled with `user_local_today`
        (the caller's local calendar date, resolved under RLS in
        `users_meta.timezone`, UTC fallback). `date` supplied (explicit or
        relative date the model computed) → used verbatim. The default path
        never routes the date through the model.
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
      * card resolution → `_resolve_proposal_card` (see its docstring).
        The agent passes `card_ref` (the short get_cards handle); a
        direct caller may pass `card_id` (a UUID). A ref/UUID that
        doesn't resolve to one of the user's active cards drops to None
        so the parse card prompts the user to pick rather than failing
        at commit time on `_assert_card_owned`.
    """
    request = ProposeTransactionRequest.model_validate(kwargs)

    # Resolve the card here — the agent passes the short `card_ref` handle
    # (robust against the UUID-transcription slips the Day 22 eval surfaced);
    # a direct/test caller may pass a `card_id` UUID. An unresolvable
    # ref/UUID drops to None so the parse card prompts the user to pick
    # rather than failing at commit on `_assert_card_owned`. The shared
    # builder takes the already-resolved card_id.
    client = supabase_for_user(user.jwt)
    card_id = _resolve_proposal_card(client, request.card_id, request.card_ref)

    # Date defaulting, categorization (+ the frozen `gemini_suggestion`
    # baseline), and the `client_request_id` mint all live in
    # build_transaction_proposal — shared with the receipt-photo path so the
    # two create surfaces can't drift. The merchant is carried through in its
    # display form (the request validator already stripped whitespace);
    # normalization for the §8.4 merchant_category key happens at confirm
    # time, not here (Day 9c canonicalization — the user should see
    # "Kentucky Fried Chicken" on the parse card, not "kentucky fried chicken").
    proposal = build_transaction_proposal(
        user,
        merchant=request.merchant,
        amount=request.amount,
        date=request.date,
        card_id=card_id,
        category=request.category,
        notes=request.notes,
        source="nlp",
    )
    return proposal.model_dump(mode="json")


class ProposeCardRequest(BaseModel):
    """Tool input for `propose_card`.

    `program` is the card's *display name* (what the user calls it,
    e.g. "Chase Sapphire Reserve") — not the rewards-program enum.
    Distinguishing here saves an awkward second tool arg; the rewards
    program (UR / MR / TYP / Bilt / Other) is what the web_search
    lookup fills in.

    `network` and `last_four` are OPTIONAL because:
      * Network is nearly always inferable from the card name (Chase
        Sapphire = Visa, Amex = Amex, etc.). The lookup fills it.
      * Last 4 is user-known but doesn't need to block the proposal
        flow; the parse-card UI collects it before commit.
    The chat system prompt teaches Claude to pass these only when the
    user explicitly said them, and never to ask for them as a blocker.

    Example request (most common):
        {"program": "Chase Sapphire Reserve"}

    Example request (with everything the user said):
        {"program": "Amex Gold", "last_four": "4321"}
    """

    model_config = ConfigDict(extra="forbid")

    network: CardNetwork | None = None
    last_four: str | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        pattern=r"^\d{4}$",
    )
    program: str
    alias: str | None = None
    # Tier 3 (DESIGN.md §6.6) — the card's region, which Claude infers from
    # the issuer/card name and passes so the lookup uses the right sources +
    # reward model. The chat path has no region selector, so this is the only
    # per-card region signal; when omitted, `propose_card` falls back to the
    # user's home-currency region. This is what makes a Japan-based user able
    # to add a US card by chat (region="US" → US lookup → issuer pins US).
    region: CardRegion | None = None
    # Day 19b — optional AF renewal date. Validated at the CardProposal
    # layer (must be >= today). When set alongside a non-zero annual_fee,
    # the confirm endpoint creates a companion subscription.
    next_annual_fee_date: _dt.date | None = None

    @field_validator("program")
    @classmethod
    def _v_program(cls, value: str) -> str:
        """Strip and reject empty card-name searches."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("program (card name) cannot be empty")
        if len(stripped) > 120:
            raise ValueError("program (card name) is unreasonably long")
        return stripped


def propose_card(user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Build a CardProposal from a chat-described credit card.

    Request:
        {"program": "Chase Sapphire Reserve",
         "network": "visa",       # optional; lookup fills if omitted
         "last_four": "1234",     # optional; parse-card UI collects later
         "alias": "travel card"}  # optional

    Response:
        CardProposal-shaped dict; see app/models/cards.py.

    The tool does NOT write to `cards`. The structural test in
    tests/contracts/test_tool_write_invariant.py enforces this — if a
    refactor here ever calls `.insert(` / `.upsert(` / `.update(` /
    `.delete(` / `.rpc(`, the test fails.

    Behavior contract:
      * `network`: request.network wins → lookup.network → "other".
      * `issuer`: lookup.issuer wins → "other" (no user-supplied path on
        the tool — the parse-card UI lets the user pick if needed).
      * `last_four`: passed through as-is (may be None). The parse-card
        UI surfaces a required input + validates before "looks right"
        enables; `POST /cards/confirm` rejects payloads still missing it.
      * `needs_manual` flips True when the lookup itself flagged it OR
        when network/issuer/last_four are still missing post-resolution
        (so the UI knows to surface the manual-fill affordances).
      * Lookup failure (provider error, parse error, etc.) returns a
        CardLookupResult with `needs_manual=True` and empty fields;
        propose_card still returns a usable shell proposal so the parse
        card can render the manual path.
    """
    request = ProposeCardRequest.model_validate(kwargs)
    client = supabase_for_user(user.jwt)
    # Region routing (Tier 3, DESIGN.md §6.6). Claude infers the card's
    # region from the issuer/card name and passes it (`request.region`); this
    # routes the lookup to the right sources + reward model and is what lets a
    # Japan-based user add a US card by chat. When Claude can't tell, we fall
    # back to the user's home-currency region (JPY→JP, TWD→TW, else US) — the
    # best remaining signal before the issuer is resolved.
    home_currency = _home_currency(client, user.user_id)
    region = request.region or region_for_currency(home_currency)
    # Adding a second copy of an existing card (same product, different
    # last_four) shouldn't surface different multipliers — Claude's
    # web_search lookup isn't deterministic across calls, and the user
    # has already seen the first card's numbers. If we already have an
    # active row with the same name, reuse its lookup-derived fields
    # instead of burning a redundant Claude+web_search call.
    reused = _existing_card_template(client, request.program)
    result = (
        reused
        if reused is not None
        else lookup_card(
            request.program, user, region=region, home_currency=home_currency
        )
    )

    network: CardNetwork = request.network or result.network or "other"
    issuer: CardIssuer = result.issuer or "other"
    program_enum: CardProgram = result.program or "Other"

    needs_manual = (
        result.needs_manual
        or (request.network is None and result.network is None)
        or (result.issuer is None)
        or (request.last_four is None)
    )

    proposal = CardProposal(
        network=network,
        last_four=request.last_four,
        name=request.program,
        issuer=issuer,
        program=program_enum,
        multipliers=result.multipliers,
        base_reward_rate=result.base_reward_rate,
        rewards_currency=result.rewards_currency,
        # Record the region the lookup used so an `other`-issuer card lands
        # with a region matching its reward shape (confirm ignores this for
        # known issuers, which it pins server-side). The chat path has no
        # region picker, so this is the home-currency-derived region.
        region=region,
        annual_fee=result.annual_fee,
        next_annual_fee_date=request.next_annual_fee_date,
        source_urls=result.source_urls,
        alias=request.alias,
        needs_manual=needs_manual,
        # Mint a stable join key the client posts back at /cards/confirm.
        # The persisted `tameru_proposal` block on `chat_messages` carries
        # this in `result.client_request_id`; the row's
        # `cards.client_request_id` column carries the same value after
        # commit, so `_annotate_committed_proposals` can join 1:1 even
        # when two cards share a `name` (different last_four). Mirrors
        # the propose_transaction lifecycle for the same id field.
        client_request_id=uuid4(),
    )
    return proposal.model_dump(mode="json")


class ProposeSubscriptionRequest(BaseModel):
    """Tool input for `propose_subscription`.

    Validates the agent's arguments before the tool runs. `card_id` is
    optional — cardless subscriptions (bank ACH bills like rent or
    utilities) are first-class. `category` is required; the agent picks
    from `ALLOWED_CATEGORIES` or asks the user if unclear.

    Example request (cardful):
        {"name": "Netflix", "amount": 15.99, "frequency": "monthly",
         "start_date": "2026-05-18", "category": "Streaming",
         "card_id": "f1e2d3c4-..."}

    Example request (cardless):
        {"name": "Rent", "amount": 2400, "frequency": "monthly",
         "start_date": "2026-05-01", "category": "Home"}
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    amount: Decimal
    frequency: Frequency
    start_date: _dt.date
    category: str
    card_id: UUID | None = None
    card_ref: str | None = None

    @field_validator("name")
    @classmethod
    def _v_name(cls, value: str) -> str:
        """Strip and reject empty subscription names."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        return stripped

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, value: Decimal) -> Decimal:
        """Reject non-positive subscription amounts."""
        if value <= 0:
            raise ValueError(f"amount must be > 0 (got {value})")
        return value

    @field_validator("category")
    @classmethod
    def _v_category(cls, value: str) -> str:
        """Reject categories outside Tameru's closed enum."""
        if value not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category {value!r} is not in the closed enum"
            )
        return value


def propose_subscription(user: AuthedUser, **kwargs: Any) -> dict[str, Any]:
    """Build a SubscriptionProposal from a chat-described recurring charge.

    Request:
        {"name": "Netflix", "amount": 15.99, "frequency": "monthly",
         "start_date": "2026-05-18", "category": "Streaming",
         "card_id": "f1e2d3c4-..."}   # card_id optional (cardless ACH)

    Response:
        SubscriptionProposal-shaped dict; see app/models/subscriptions.py.

    The tool does NOT write to `subscriptions`. The structural test in
    tests/contracts/test_tool_write_invariant.py enforces this — if a
    refactor here ever calls `.insert(` / `.upsert(` / `.update(` /
    `.delete(` / `.rpc(`, the test fails.

    Behavior contract:
      * `next_billing_date` is computed by `compute_next_billing_date` —
        forward-only (DESIGN.md §8.3). If `start_date <= today`, the
        first auto-log fires on `today + 1 period`; past cycles are NOT
        backfilled. If `start_date > today`, `next_billing_date = start_date`.
      * `client_request_id` is freshly minted (`uuid4()`) — same shape
        as `propose_transaction` and `propose_card`. The Day 15 offline
        queue carries it opaquely across drain retries; the partial
        unique index on `subscriptions (user_id, client_request_id)`
        makes a same-crid replay a no-op at confirm time.
      * card resolution → `_resolve_proposal_card`. The agent passes
        `card_ref` (the short get_cards handle); a direct caller may
        pass `card_id` (a UUID). A ref/UUID that doesn't resolve to one
        of the user's active cards drops to None so the parse card
        surfaces a picker instead of failing at commit on
        `_assert_card_owned`.
      * card omitted → cardless subscription (bank ACH); the confirm
        endpoint allows this and pg_cron auto-logs with
        `transactions.card_id = NULL`.
    """
    request = ProposeSubscriptionRequest.model_validate(kwargs)

    client = supabase_for_user(user.jwt)
    card_id = _resolve_proposal_card(client, request.card_id, request.card_ref)

    next_billing_date = compute_next_billing_date(
        request.start_date,
        request.frequency,
        # User-local today: a JST user's "starting today" must hit the
        # forward-only clamp, not the future-start branch (audit P3-29).
        today=user_local_today(user.jwt),
    )

    proposal = SubscriptionProposal(
        name=request.name,
        amount=request.amount,
        frequency=request.frequency,
        start_date=request.start_date,
        next_billing_date=next_billing_date,
        category=request.category,
        card_id=card_id,
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
    PROPOSE_CARD_TOOL["name"]: (PROPOSE_CARD_TOOL, propose_card),
    PROPOSE_SUBSCRIPTION_TOOL["name"]: (
        PROPOSE_SUBSCRIPTION_TOOL,
        propose_subscription,
    ),
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


def _months_spanned(start: _dt.date, end: _dt.date) -> int:
    """Count the calendar months an inclusive [start, end] range touches.

    March-only (Mar 1 → Mar 31) spans 1; Feb 1 → Mar 31 spans 2. Clamped
    to at least 1 so an inverted or single-day range still reports a
    sane `window_months` in the spending-summary response.
    """
    raw = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return max(1, raw)


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


def _fetch_aggregation_pages(
    build_query: Any, *, cap: int, page_size: int | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Page through an aggregation read, returning (rows, truncated).

    Exists because PostgREST silently caps every response at `max-rows`
    (1000 on Supabase) with no error — a single `.range(0, cap)` request
    with cap > 1000 comes back with at most 1000 rows, the Python-side
    sum covers an arbitrary subset, and the `len(rows) > cap` truncation
    sentinel can never fire (the P1 wrong-money bug). Paging at
    ≤ `max-rows` per page is the only way to actually see all rows.

    `build_query` is a zero-arg callable returning a fresh filtered query
    builder — `.range()` must be applied to a fresh builder per page
    (the goals.py `_sum_active_transactions` precedent).

    Truncation semantics: rows beyond `cap` are discarded and
    `truncated=True` is returned, so callers keep the exact
    partial-sum-plus-flag contract they had before.
    """
    if page_size is None:
        # Read the module global at call time (not a def-time default) so
        # tests can monkeypatch the page size.
        page_size = AGGREGATION_PAGE_SIZE
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        resp = build_query().range(start, start + page_size - 1).execute()
        page = resp.data or []
        rows.extend(page)
        if len(rows) > cap:
            return rows[:cap], True
        if len(page) < page_size:
            return rows, False
        start += page_size


def _strip_keys(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Drop redundant keys from a tool response.

    RLS already scopes by `user_id`, so emitting it on every row just
    burns tokens. Same for any per-row metadata Claude won't reason
    about.
    """
    return {k: v for k, v in row.items() if k not in keys}


def _home_currency(client: Any, user_id: Any) -> str:
    """Read the user's immutable home_currency for Tier 3 region routing.

    `home_currency` is fixed at signup (invariant 13). Falls back to "USD"
    (US routing) when no `users_meta` row exists yet — a card proposal
    before onboarding completes shouldn't error. Mirrors the same-named
    helper in `app/routes/cards.py`. RLS scopes the read to the caller.
    """
    resp = (
        client.table("users_meta")
        .select("home_currency")
        .eq("user_id", str(user_id))
        .execute()
    )
    if resp.data and resp.data[0].get("home_currency"):
        return resp.data[0]["home_currency"]
    return "USD"


def _existing_card_template(client: Any, program: str) -> CardLookupResult | None:
    """Reuse a same-name active card's lookup so a second copy matches.

    When the user adds a second copy of a product they already own (same
    name, different last_four), running a fresh `lookup_card` produces a
    new proposal whose multipliers / annual_fee / source_urls may drift
    from the first — Claude's `web_search` answer isn't deterministic
    across calls. That surfaces as "why do my two Sapphire Reserves earn
    different points?" in the UI and burns a redundant Claude API call.

    Cards are bounded to ~10 per user lifetime (DESIGN.md §8.1), so an
    unfiltered scan + Python-side case-insensitive name compare is cheap
    and dodges `ilike` metacharacter pitfalls on user-supplied strings.
    Returns None if no same-name active card exists; caller falls back to
    `lookup_card`.
    """
    target = program.strip().lower()
    if not target:
        return None
    resp = (
        client.table("cards")
        .select(
            "name, issuer, network, program, multipliers, annual_fee, "
            "source_urls, base_reward_rate, rewards_currency"
        )
        .eq("status", "active")
        .execute()
    )
    for row in resp.data or []:
        if (row.get("name") or "").strip().lower() != target:
            continue
        return CardLookupResult(
            program=row.get("program"),
            network=row.get("network"),
            issuer=row.get("issuer"),
            multipliers=row.get("multipliers") or {},
            annual_fee=row.get("annual_fee"),
            source_urls=row.get("source_urls") or [],
            base_reward_rate=row.get("base_reward_rate"),
            rewards_currency=row.get("rewards_currency"),
            needs_manual=False,
        )
    return None


def _card_belongs_to_user(client: Any, card_id: UUID) -> bool:
    """Defensive check used by `propose_transaction`.

    Returns True iff the RLS-scoped client sees the card AND the card has
    `status = 'active'`. Hallucinated UUIDs, cross-user UUIDs, and
    soft-deleted cards (`status='deleted'`, see DESIGN.md §8.1) all return
    False. The status filter matters because `get_cards` only returns
    active cards — but stale conversation history can still surface a
    deleted card's UUID to Claude, and we don't want a chat-typed
    transaction to be silently posted against a card the user closed.
    Mirrors the confirm-side `_assert_card_owned` (app/routes/transactions.py)
    so propose and confirm agree on which cards are usable.
    """
    resp = (
        client.table("cards")
        .select("id")
        .eq("id", str(card_id))
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def _card_ref(issuer: Any, last_four: Any, card_id: Any) -> str:
    """Build the short card handle the agent copies between tool calls.

    `{issuer}-{last_four}` (e.g. "amex-1001"). Issuer enum values use
    underscores, never hyphens, so the handle round-trips unambiguously
    through `_resolve_card_ref`'s `rpartition("-")`. Falls back to the
    raw UUID only for the rare card with no `last_four` — `get_cards`
    returns confirmed cards, which always have one.
    """
    if issuer and last_four:
        return f"{issuer}-{last_four}"
    return str(card_id)


def _resolve_card_ref(client: Any, ref: str) -> str | None:
    """Resolve a card-reference string to the card's UUID, or None.

    Accepts the `{issuer}-{last_four}` handle `get_cards` emits, and —
    defensively — a raw UUID (the fallback handle for a card with no
    `last_four`). Resolution is RLS-scoped: a ref that matches no active
    card of the caller returns None, which the proposers treat as
    "no card" (the parse card then prompts the user to pick). A slip in
    the handle fails closed this way rather than mis-resolving.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    # Raw-UUID fallback first — a card whose handle is its UUID.
    try:
        as_uuid = UUID(ref)
    except ValueError:
        as_uuid = None
    if as_uuid is not None:
        return str(as_uuid) if _card_belongs_to_user(client, as_uuid) else None
    # `{issuer}-{last_four}` handle. Issuer values never contain a
    # hyphen, so the LAST hyphen separates issuer from last_four.
    issuer, sep, last_four = ref.rpartition("-")
    if not (sep and issuer and last_four):
        return None
    resp = (
        client.table("cards")
        .select("id")
        .eq("issuer", issuer)
        .eq("last_four", last_four)
        .eq("status", "active")
        .limit(2)
        .execute()
    )
    rows = resp.data or []
    # The natural-key partial unique index guarantees ≤1 active match;
    # `limit(2)` is belt-and-suspenders so a hypothetical dup resolves to
    # None (ambiguous) rather than silently picking one.
    return rows[0]["id"] if len(rows) == 1 else None


def _card_refs_by_id(client: Any) -> dict[str, str]:
    """Map the user's card UUIDs to their short `{issuer}-{last_four}` refs.

    Used by `get_subscriptions` to annotate each row with a model-safe
    `card_ref` instead of exposing the raw `card_id` UUID. Includes
    non-active cards too: a paused subscription can still point at a
    soft-deleted card, and showing its ref beats a null. RLS scopes the
    read to the caller; one query per tool call, bounded by the <10-cards
    v1 reality.
    """
    resp = client.table("cards").select("id, issuer, last_four").execute()
    return {
        row["id"]: _card_ref(row.get("issuer"), row.get("last_four"), row.get("id"))
        for row in (resp.data or [])
    }


def _resolve_card_ref_filter(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a `card_ref` READ-filter to `card_id`, failing loudly.

    Read filters need the opposite failure mode from proposals: a
    proposal with an unresolvable ref degrades to cardless and the user
    corrects it on the parse card, but a filter that silently matches
    nothing returns `{total: "0"}` presented as exact — the model then
    confidently tells the user they spent $0 on that card (audit P2-9).
    So an unresolvable ref raises ValueError, which the agent loop
    surfaces as an `is_error` tool_result the model can react to (re-call
    get_cards, copy the ref exactly).

    Returns the payload with `card_ref` removed and `card_id` injected
    when a ref was present; unchanged otherwise.
    """
    ref = payload.pop("card_ref", None)
    if ref is None:
        return payload
    card_id = _resolve_card_ref(client, ref)
    if card_id is None:
        raise ValueError(
            f"card_ref {ref!r} does not match any of the user's active cards. "
            "Call get_cards and copy the `ref` value exactly."
        )
    payload["card_id"] = card_id
    return payload


def _resolve_proposal_card(
    client: Any, card_id: UUID | None, card_ref: str | None
) -> str | None:
    """Resolve the card a proposal should carry, or None.

    `card_ref` (the short get_cards handle) is the agent's path and is
    preferred when present — it's robust against the UUID-transcription
    slips the Day 22 eval surfaced. `card_id` (a raw UUID) is the
    direct-caller / test path, validated via `_card_belongs_to_user`.
    Either input that fails to resolve to one of the user's active cards
    yields None — proposers then leave the proposal cardless and the
    parse card prompts the user to pick.
    """
    if card_ref is not None:
        return _resolve_card_ref(client, card_ref)
    if card_id is not None and _card_belongs_to_user(client, card_id):
        return str(card_id)
    return None
