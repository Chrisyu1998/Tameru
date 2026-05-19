"""Subscription request/response models — Day 19.

`SubscriptionProposal` is produced by Day 19's `propose_subscription` tool
and consumed by `POST /subscriptions/confirm`. Defining it here keeps the
wire shape and the tool return shape identical — the same dual-role pattern
as `TransactionProposal` and `CardProposal`.

All monetary amounts use `Decimal` for `numeric` column round-trip safety
(DESIGN.md §8.2, invariant 13).

`card_id` is nullable — cardless subscriptions (bank ACH bills like rent or
utilities) auto-log transactions with `card_id = NULL`. DESIGN.md §8.3.

`frequency` and `start_date` are IMMUTABLE post-create (§8.3). The PATCH
model below rejects them.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.prompts.categories import ALLOWED_CATEGORIES


Frequency = Literal["monthly", "quarterly", "annual", "weekly"]
SubscriptionStatus = Literal["active", "paused", "cancelled"]


class SubscriptionProposal(BaseModel):
    """Wire shape for a chat-originated subscription create.

    Dual-role:

      1. **Agent tool return.** `propose_subscription({name, amount,
         frequency, start_date, category, card_id?})` computes
         `next_billing_date` from `start_date + frequency` using the
         forward-only rule (§8.3): if `start_date <= today`,
         `next_billing_date = today + 1 period`; else
         `next_billing_date = start_date`. The tool mints a fresh
         `client_request_id` and returns this shape. NO row written.
      2. **Endpoint body.** `POST /subscriptions/confirm` accepts the same
         shape verbatim after the user taps "looks right."

    `category` is REQUIRED. `card_id` is optional — omit for ACH bills
    (rent, utilities, mortgage). When set, the confirm endpoint validates
    ownership before insert.

    Example request (cardful):
        {"name": "Netflix", "amount": 15.99, "frequency": "monthly",
         "start_date": "2026-05-18", "next_billing_date": "2026-06-18",
         "category": "Streaming", "card_id": "f1e2d3c4-...",
         "client_request_id": "..."}

    Example request (cardless / ACH):
        {"name": "Rent", "amount": 2400, "frequency": "monthly",
         "start_date": "2026-05-01", "next_billing_date": "2026-06-01",
         "category": "Home", "client_request_id": "..."}
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    amount: Decimal
    frequency: Frequency
    start_date: _dt.date
    next_billing_date: _dt.date
    category: str
    card_id: UUID | None = None
    client_request_id: UUID

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
                f"category {value!r} is not in the closed enum "
                f"(see app/prompts/categories.py)"
            )
        return value


class SubscriptionConfirmRequest(SubscriptionProposal):
    """HTTP body for `POST /subscriptions/confirm`.

    Same shape as `SubscriptionProposal` today — split out so the OpenAPI
    docs reflect the endpoint's role, and so HTTP-only validators (if any
    ever materialize) have somewhere to live without touching the proposal
    shape that the agent tool also returns.
    """

    pass


class SubscriptionPatchRequest(BaseModel):
    """Partial update body for `PATCH /subscriptions/{id}`.

    Editable: `amount`, `category`, `name`, `card_id`, `status`.
    Rejected with 422: `frequency`, `start_date` — per the §8.3
    immutability rule. To change cadence, cancel and re-add.

    Pydantic's `extra='forbid'` means a client that sends `frequency`
    or `start_date` is rejected at the model layer with a clear error,
    not silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    amount: Decimal | None = None
    category: str | None = None
    card_id: UUID | None = None
    status: SubscriptionStatus | None = None

    @field_validator("name")
    @classmethod
    def _vp_name(cls, v: str | None) -> str | None:
        """Strip non-null name patches and reject empty names."""
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        return stripped

    @field_validator("amount")
    @classmethod
    def _vp_amount(cls, v: Decimal | None) -> Decimal | None:
        """Reject non-null amount patches that are not positive."""
        if v is not None and v <= 0:
            raise ValueError(f"amount must be > 0 (got {v})")
        return v

    @field_validator("category")
    @classmethod
    def _vp_category(cls, v: str | None) -> str | None:
        """Reject non-null category patches outside the closed enum."""
        if v is not None and v not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category {v!r} is not in the closed enum"
            )
        return v


class SubscriptionRow(BaseModel):
    """Response shape for a single `subscriptions` row.

    Mirrors the `subscriptions` table after the Day 19 migrations.
    `card_id` is `UUID | None` (cardless ACH subscriptions);
    `client_request_id` is `UUID | None` (pg_cron-written rows leave it
    NULL).
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    card_id: UUID | None
    name: str
    amount: Decimal
    frequency: Frequency
    start_date: _dt.date
    next_billing_date: _dt.date
    category: str
    status: SubscriptionStatus
    client_request_id: UUID | None
    created_at: _dt.datetime


class SubscriptionListResponse(BaseModel):
    """`GET /subscriptions` response.

    No pagination — subscriptions are bounded to ~tens per user at v1
    scale, and `get_subscriptions` (agent tool) already caps at
    SUBSCRIPTIONS_ROW_CAP in app/agent/tools.py.
    """

    items: list[SubscriptionRow]


def compute_next_billing_date(start_date: _dt.date, frequency: Frequency) -> _dt.date:
    """Forward-only next-billing-date computation (§8.3).

    At create time:
      - if start_date <= today: next_billing_date = today + 1 period
      - if start_date >  today: next_billing_date = start_date

    Past cycles are never backfilled — manual transaction entry is the
    escape hatch for historical charges. Matches the YNAB / Copilot /
    Rocket Money / Monarch pattern (none of them backfill on a backdated
    start_date).
    """
    today = _dt.date.today()
    anchor = today if start_date <= today else start_date
    return _advance_period(anchor, frequency) if start_date <= today else start_date


def _advance_period(d: _dt.date, frequency: Frequency) -> _dt.date:
    """Advance a date by one period of the given frequency.

    Pure stdlib — avoids dragging in dateutil for this one call site.
    Month / quarter arithmetic uses calendar months anchored at the day
    of the month (matching the pg_cron SQL function's CASE on frequency).
    """
    if frequency == "weekly":
        return d + _dt.timedelta(days=7)
    if frequency == "monthly":
        return _add_months(d, 1)
    if frequency == "quarterly":
        return _add_months(d, 3)
    return _add_months(d, 12)


def _add_months(d: _dt.date, months: int) -> _dt.date:
    """Add `months` calendar months, preserving day-of-month when possible."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    # Clamp day to the last day of the new month (Feb 30 → Feb 28/29).
    day = min(d.day, _days_in_month(year, month))
    return _dt.date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    """Return the number of days in `(year, month)` using stdlib calendar."""
    import calendar

    return calendar.monthrange(year, month)[1]
