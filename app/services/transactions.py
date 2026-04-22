"""Transaction query service — the one source of truth for list reads.

`list_transactions(user, filters)` is the function Day 5's `GET /transactions`
handler wraps AND Day 9's `get_transactions` agent tool calls directly. Both
callers reach the same query builder so the HTTP shape and the agent-tool
shape cannot drift.

`TransactionFilters`, `DEFAULT_LIMIT`, and `MAX_LIMIT` live in
`app/models/transactions.py` — the filter type is as much a shared request
shape as `TransactionProposal` is, so it belongs in the models module
regardless of which function consumes it first.

RLS enforces `user_id = auth.uid()` so the query omits a `WHERE user_id = ?`
clause deliberately — the RLS contract test (tests/test_rls_contract.py) is
what guarantees that's safe.
"""

from __future__ import annotations

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.models.transactions import (
    MAX_LIMIT,
    TransactionFilters,
    TransactionListResponse,
    TransactionRow,
)


def list_transactions(
    user: AuthedUser, filters: TransactionFilters
) -> TransactionListResponse:
    """Return a page of the user's transactions matching `filters`.

    The page is at most `MAX_LIMIT` rows regardless of the `filters.limit`
    value the caller supplied — clamping is silent. `has_more` is computed
    by requesting one extra row than the clamped limit; if the extra row
    materializes, we drop it and set `has_more=True`.
    """
    effective_limit = min(filters.limit, MAX_LIMIT)
    client = supabase_for_user(user.jwt)

    query = (
        client.table("transactions")
        .select("*")
        # date DESC, created_at DESC matches the transactions_user_date_idx
        # index's leading key and gives deterministic ordering when two
        # rows share a date. Prompted tests rely on this.
        .order("date", desc=True)
        .order("created_at", desc=True)
    )

    if filters.card_id is not None:
        query = query.eq("card_id", str(filters.card_id))
    if filters.category is not None:
        query = query.eq("category", filters.category)
    if filters.merchant_contains:
        # ILIKE substring — unindexed scan, fine at v1 scale. A pg_trgm
        # index is a Phase 2 optimization if/when usage proves it out.
        query = query.ilike("merchant", f"%{filters.merchant_contains}%")
    if filters.date_from is not None:
        query = query.gte("date", filters.date_from.isoformat())
    if filters.date_to is not None:
        query = query.lte("date", filters.date_to.isoformat())
    if filters.amount_min is not None:
        query = query.gte("amount", str(filters.amount_min))
    if filters.amount_max is not None:
        query = query.lte("amount", str(filters.amount_max))

    # Fetch one extra to detect `has_more` without a separate COUNT query.
    start = filters.offset
    end = start + effective_limit  # inclusive end ⇒ effective_limit + 1 rows
    resp = query.range(start, end).execute()
    rows = resp.data or []

    has_more = len(rows) > effective_limit
    if has_more:
        rows = rows[:effective_limit]

    items = [TransactionRow.model_validate(row) for row in rows]
    return TransactionListResponse(items=items, has_more=has_more)
