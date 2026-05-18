"""Goal request/response models.

`SetGoalRequest` is the validated input to the `set_goal` agent tool;
`Goal` is the row shape returned by the upsert. Both keep the closed-enum /
positive-amount validation in one place so the agent tool and the
`GET/PATCH/DELETE /goals` HTTP routes share validation.

Amounts are `Decimal` to match the `numeric` column type. The closed
`period` enum is enforced both as a Pydantic `Literal` (caller-visible
validation error) and as a Postgres CHECK constraint (defense-in-depth).

`GoalPatchRequest` / `GoalWithSpend` / `GoalsListResponse` back the HTTP
routes — `PATCH` permits amount + period only (category is fixed by the
unique key; to move a goal between categories, delete and re-create via
chat), and `GET` carries the per-goal period-to-date spend so the
frontend's progress bars don't need a second round-trip.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.prompts.categories import ALLOWED_CATEGORIES


GoalPeriod = Literal["week", "month", "year"]


class SetGoalRequest(BaseModel):
    """Validated input for `set_goal`.

    `category=None` is a legitimate input — it encodes the overall budget
    across all categories. The migration's `NULLS NOT DISTINCT` constraint
    makes the (user, NULL, period) slot upsertable as a single row.
    """

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(
        default=None,
        description="Closed-enum category, or None for an overall budget.",
    )
    amount: Decimal = Field(description="Budget amount; must be > 0.")
    period: GoalPeriod = Field(description="Budget window: week, month, or year.")

    @field_validator("category")
    @classmethod
    def _v_category(cls, value: str | None) -> str | None:
        """Reject categories outside Tameru's closed enum, allow None."""
        if value is not None and value not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category {value!r} is not in the closed enum (see app/prompts/categories.py)"
            )
        return value

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, value: Decimal) -> Decimal:
        """Reject non-positive goal amounts at the model layer."""
        if value <= 0:
            raise ValueError(f"amount must be > 0 (got {value})")
        return value


class Goal(BaseModel):
    """One `goals` row, returned by the upsert and by `GET /goals`.

    Mirrors the table exactly. `category` may be NULL — see SetGoalRequest.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    category: str | None
    amount: Decimal
    period: GoalPeriod
    created_at: _dt.datetime
    updated_at: _dt.datetime


class GoalPatchRequest(BaseModel):
    """Patch body for `PATCH /goals/{id}` — amount and/or period.

    Both fields optional, at least one required. Category is intentionally
    omitted: the `(user_id, category, period)` unique key means a category
    change is indistinguishable from creating a new goal, so callers
    delete + ask chat to set a new one in the desired slot.
    """

    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(default=None)
    period: GoalPeriod | None = Field(default=None)

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, value: Decimal | None) -> Decimal | None:
        """Reject non-positive amounts at the model layer."""
        if value is not None and value <= 0:
            raise ValueError(f"amount must be > 0 (got {value})")
        return value

    @model_validator(mode="after")
    def _v_at_least_one(self) -> GoalPatchRequest:
        """Require at least one writable field — empty PATCH is a 422."""
        if self.amount is None and self.period is None:
            raise ValueError("at least one of amount, period must be provided")
        return self


class GoalWithSpend(BaseModel):
    """A Goal plus the period-to-date spend that fills its progress bar.

    `spent_period_to_date` is the sum over `active_transactions` filtered
    by category (overall budgets sum across all categories) and the
    calendar-aligned window for the goal's period. `progress_ratio` is a
    convenience for the frontend so it doesn't divide Decimals client-side
    — clamped to [0.0, ∞); over-budget renders as > 1.0.
    """

    model_config = ConfigDict(extra="ignore")

    goal: Goal
    spent_period_to_date: Decimal
    window_start: _dt.date
    window_end: _dt.date
    progress_ratio: float


class GoalsListResponse(BaseModel):
    """List response shape for `GET /goals`."""

    items: list[GoalWithSpend]
