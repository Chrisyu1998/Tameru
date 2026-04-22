"""Shared transaction request/response models — Day 5 + Day 9.

`TransactionProposal` is produced by Day 9's `propose_transaction` tool and
consumed by Day 5's `POST /transactions/confirm` endpoint. Defining it here
keeps the wire shape and the tool return shape identical — when Day 9 lands,
the tool imports from this module rather than redefining the fields.

All monetary amounts are `Decimal` to match the `numeric` column type in
Postgres. Floats do not belong on this path (DESIGN.md §8.2, invariant 13).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.prompts.categories import ALLOWED_CATEGORIES


def _validate_category(value: str) -> str:
    if value not in ALLOWED_CATEGORIES:
        raise ValueError(
            f"category {value!r} is not in the closed enum (see app/prompts/categories.py)"
        )
    return value


def _validate_positive_amount(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError(f"amount must be > 0 (got {value})")
    return value


def _validate_merchant_nonblank(value: str) -> str:
    """Strip leading/trailing whitespace and reject empty/whitespace-only.

    `Field(min_length=1)` counts characters, not meaningful content — '   '
    passes that check. We trim on the way in (trailing whitespace is almost
    never intended user input) and reject the all-whitespace case outright.
    Internal whitespace in a merchant name like 'Trader Joes' is preserved.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("merchant cannot be empty or whitespace-only")
    return stripped


class TransactionProposal(BaseModel):
    """Wire shape for a chat-originated transaction create.

    One class, two roles — intentional:

      1. **Agent tool return.** Day 9's `propose_transaction` tool builds a
         `TransactionProposal` from the user's chat message (fills merchant
         / amount / date / card_id, calls `categorize()` for `category`,
         mints a fresh `client_request_id`) and returns it as the tool
         result. The client renders it as a parse card (UX frame 15).
      2. **Endpoint body.** `POST /transactions/confirm` accepts the same
         shape — the client posts the proposal back verbatim after the
         user taps "looks right" (with any inline field edits applied).

    Keeping one class means the tool and the endpoint cannot drift on wire
    shape. If they need to diverge later, split then — not pre-emptively.

    There is no `source` field — the server hardcodes `"nlp"` on the
    confirm path. CSV import and the pg_cron auto-logger write at the SQL
    layer with their own `source` values.
    """

    model_config = ConfigDict(extra="forbid")

    merchant: str
    amount: Decimal
    date: _dt.date
    card_id: UUID | None = None
    category: str
    notes: str | None = None
    gemini_suggestion: str | None = None
    client_request_id: UUID

    _v_merchant = field_validator("merchant")(_validate_merchant_nonblank)
    _v_category = field_validator("category")(_validate_category)
    _v_amount = field_validator("amount")(_validate_positive_amount)


class TransactionConfirmRequest(TransactionProposal):
    """HTTP body for `POST /transactions/confirm`.

    Identical in shape to `TransactionProposal` today — the client posts
    the proposal back verbatim after the user taps "looks right." This
    exists as a separate class so:

      * OpenAPI documents the endpoint's input contract by its HTTP role
        ("TransactionConfirmRequest"), not by its domain role
        ("TransactionProposal").
      * The tool return type and the endpoint body evolve independently.
        A validator that only makes sense at the HTTP boundary (e.g. "the
        proposal's implicit timestamp must be within 24h") lives here;
        one that's about the domain object ("amount must be positive")
        lives on `TransactionProposal` and is inherited.

    When the shapes genuinely diverge, stop inheriting and declare fields
    directly. Until then, empty subclass is the minimal honest statement
    of "the wire shape is a proposal, but the contract is our own."
    """

    pass


class TransactionPatchRequest(BaseModel):
    """Partial update body for `PATCH /transactions/{id}`.

    Every field optional; the handler applies only the present keys. Used
    by the edit sheet (Day 15) and — in a post-launch enhancement — by an
    inline chat update-confirm card (DESIGN.md §6.2).
    """

    model_config = ConfigDict(extra="forbid")

    merchant: str | None = None
    amount: Decimal | None = None
    date: _dt.date | None = None
    card_id: UUID | None = None
    category: str | None = None
    notes: str | None = None

    @field_validator("merchant")
    @classmethod
    def _vp_merchant(cls, v: str | None) -> str | None:
        return None if v is None else _validate_merchant_nonblank(v)

    @field_validator("category")
    @classmethod
    def _vp_category(cls, v: str | None) -> str | None:
        return None if v is None else _validate_category(v)

    @field_validator("amount")
    @classmethod
    def _vp_amount(cls, v: Decimal | None) -> Decimal | None:
        return None if v is None else _validate_positive_amount(v)


class TransactionRow(BaseModel):
    """Response shape for a single transaction row.

    Mirrors the `transactions` table exactly (DESIGN.md §8.2). No client
    transformation — Supabase returns the row, we pass it through with
    typed fields.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    card_id: UUID | None
    subscription_id: UUID | None
    merchant: str
    amount: Decimal
    date: _dt.date
    category: str
    gemini_suggestion: str | None
    source: str
    notes: str | None
    client_request_id: UUID | None
    created_at: _dt.datetime
    updated_at: _dt.datetime


class TransactionConfirmResponse(BaseModel):
    """`POST /transactions/confirm` response.

    `insight` is always `None` in Day 5's implementation (Day 13 wires in
    `entry_moment_insight()`). On idempotent replay (same
    `client_request_id`) `insight` stays `None` even after Day 13 — the
    original insight already fired on the first confirm.
    """

    transaction: TransactionRow
    insight: str | None = None


class TransactionListResponse(BaseModel):
    """`GET /transactions` response + `list_transactions()` service return.

    No `total` field — computing it requires a separate COUNT and the
    consumers (infinite-scroll list UX, agent disambiguation) only need
    `has_more` (Day 5 prompt).
    """

    items: list[TransactionRow]
    has_more: bool


# Pagination limits — shared between the Pydantic field default, the route
# handler's Query(default=...), and the service's silent clamp. Kept here
# (rather than in services/transactions.py) so Day 9's agent tool imports
# the same values without reaching into a services module, and so
# TransactionFilters's default is defined alongside the constant it uses.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


class TransactionFilters(BaseModel):
    """Query parameters for `list_transactions`.

    Same dual-role pattern as `TransactionProposal`: the HTTP route
    handler builds one of these from URL query params, and Day 9's
    `get_transactions` agent tool builds the same type from its tool
    arguments. Both hand it to `list_transactions()` (services layer).

    Not named `TransactionListRequest` because there is no JSON request
    body to mirror — GET queries are URL params. A named type exists
    because two callers (route + agent tool) must agree on the filter
    shape.
    """

    model_config = ConfigDict(extra="forbid")

    card_id: UUID | None = None
    category: str | None = None
    merchant_contains: str | None = None
    date_from: _dt.date | None = None
    date_to: _dt.date | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=1)
    offset: int = Field(default=0, ge=0)
