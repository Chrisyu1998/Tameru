"""Goal request/response models — Day 9b.

`SetGoalRequest` is the validated input to the `set_goal` agent tool;
`Goal` is the row shape returned by the upsert. Defining both here keeps
the wire shape stable if a future GET /goals endpoint reuses `Goal` and
keeps the closed-enum / positive-amount validation in one place.

Amounts are `Decimal` to match the `numeric` column type. The closed
`period` enum is enforced both as a Pydantic `Literal` (caller-visible
validation error) and as a Postgres CHECK constraint (defense-in-depth).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """One `goals` row, returned by the upsert and by future GET /goals.

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
