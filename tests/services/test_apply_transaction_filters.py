"""Unit test for the shared filter applier — Day 9a.

`apply_transaction_filters` is the seam that prevents drift between the
HTTP list endpoint and the agent's `get_transactions` / `calculate_total`
tools. A regression that re-introduces divergent filter codepaths would
silently break "how much at Trader Joe's this month" the way it did
before Day 9a.

Doesn't touch Supabase — uses a recording stub for the PostgREST builder.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.models.transactions import TransactionFilters
from app.services.transactions import apply_transaction_filters


@dataclass
class _RecordingBuilder:
    """Captures every builder call so the test can assert which filters
    were applied with which arguments."""

    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def _record(self, op: str, *args: Any) -> "_RecordingBuilder":
        """Support record."""
        self.calls.append((op, args))
        return self

    def eq(self, col: str, value: Any) -> "_RecordingBuilder":
        """Provide eq."""
        return self._record("eq", col, value)

    def ilike(self, col: str, pattern: str) -> "_RecordingBuilder":
        """Provide ilike."""
        return self._record("ilike", col, pattern)

    def gte(self, col: str, value: Any) -> "_RecordingBuilder":
        """Provide gte."""
        return self._record("gte", col, value)

    def lte(self, col: str, value: Any) -> "_RecordingBuilder":
        """Provide lte."""
        return self._record("lte", col, value)


def test_empty_filters_apply_no_constraints():
    """Verify that empty filters apply no constraints."""
    builder = _RecordingBuilder()
    result = apply_transaction_filters(builder, TransactionFilters())
    assert result is builder
    assert builder.calls == []


def test_each_filter_applies_exactly_once():
    """Verify that each filter applies exactly once."""
    card_id = uuid4()
    builder = _RecordingBuilder()
    filters = TransactionFilters(
        card_id=card_id,
        category="Dining",
        merchant_contains="trader",
        date_from=_dt.date(2026, 4, 1),
        date_to=_dt.date(2026, 4, 30),
        amount_min=Decimal("5"),
        amount_max=Decimal("100"),
    )
    apply_transaction_filters(builder, filters)
    ops = [op for op, _ in builder.calls]
    # Exactly one builder call per set filter. No surprises like double
    # application or accidental .or_() chains.
    assert ops.count("eq") == 2  # card_id, category
    assert ops.count("ilike") == 1  # merchant_contains
    assert ops.count("gte") == 2  # date_from, amount_min
    assert ops.count("lte") == 2  # date_to, amount_max
    # Exact arg shapes that matter for the wire contract.
    args_by_op = {(op, args[0]): args for op, args in builder.calls}
    assert args_by_op[("eq", "card_id")] == ("card_id", str(card_id))
    assert args_by_op[("eq", "category")] == ("category", "Dining")
    assert args_by_op[("ilike", "merchant")] == ("merchant", "%trader%")
    assert args_by_op[("gte", "date")] == ("date", "2026-04-01")
    assert args_by_op[("lte", "date")] == ("date", "2026-04-30")
    # Decimals are passed as strings — matches the `numeric` column type.
    assert args_by_op[("gte", "amount")] == ("amount", "5")
    assert args_by_op[("lte", "amount")] == ("amount", "100")


def test_empty_string_merchant_contains_is_skipped():
    """`merchant_contains=""` is treated as "no constraint" — the truthy
    check in `apply_transaction_filters` ensures a fall-through to the
    bare query, not an ILIKE on `%%` (which would match everything but
    waste a planner cycle and noise the audit trail)."""
    builder = _RecordingBuilder()
    filters = TransactionFilters(merchant_contains="")
    apply_transaction_filters(builder, filters)
    assert builder.calls == []
