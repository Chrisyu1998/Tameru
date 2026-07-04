"""Transaction query service — the one source of truth for list reads.

`list_transactions(user, filters)` is the function Day 5's `GET /transactions`
handler wraps AND Day 9a's `get_transactions` agent tool calls directly. Both
callers reach the same query builder so the HTTP shape and the agent-tool
shape cannot drift.

`apply_transaction_filters(query, filters)` is the shared filter applier —
extracted in Day 9a so `calculate_total` (agent tool) and `list_transactions`
go through one filter codepath. Adding a filter dimension is one change in
one place.

`TransactionFilters`, `DEFAULT_LIMIT`, and `MAX_LIMIT` live in
`app/models/transactions.py` — the filter type is as much a shared request
shape as `TransactionProposal` is, so it belongs in the models module
regardless of which function consumes it first.

RLS enforces `user_id = auth.uid()` so the query omits a `WHERE user_id = ?`
clause deliberately — the RLS contract test (tests/test_rls_contract.py) is
what guarantees that's safe.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.integrations.gemini import GeminiError, categorize
from app.models.transactions import (
    MAX_LIMIT,
    TransactionFilters,
    TransactionListResponse,
    TransactionProposal,
    TransactionRow,
)
from app.util.timezone import user_local_today


def apply_transaction_filters(query: Any, filters: TransactionFilters) -> Any:
    """Apply every set filter to a PostgREST select builder.

    Returns the (mutated) builder so callers can chain `.order()` /
    `.range()` after. Filters that are `None` (or empty string for
    `merchant_contains`) are skipped — they mean "no constraint." Mirrors
    the field-by-field application that previously lived inline in
    `list_transactions`.
    """
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
    return query


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
        # `active_transactions` is the default-safe read surface (DESIGN.md
        # §8 status-column doctrine): soft-deleted rows do not appear in any
        # listing/agent/dashboard read by going through this view. Writes
        # still target the base `transactions` table.
        client.table("active_transactions")
        .select("*")
        # date DESC, created_at DESC matches the transactions_user_date_idx
        # index's leading key and gives deterministic ordering when two
        # rows share a date. Prompted tests rely on this.
        .order("date", desc=True)
        .order("created_at", desc=True)
    )
    query = apply_transaction_filters(query, filters)

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


def build_transaction_proposal(
    user: AuthedUser,
    *,
    merchant: str,
    amount: Decimal,
    date: _dt.date | None = None,
    card_id: str | UUID | None = None,
    category: str | None = None,
    notes: str | None = None,
    source: Literal["nlp", "receipt_photo"] = "nlp",
) -> TransactionProposal:
    """Build a `TransactionProposal` from resolved fields.

    The shared core of Day 9's `propose_transaction` agent tool (chat) and the
    `POST /receipts/parse` route (receipt photo), so the two create surfaces
    cannot drift on how date defaulting, categorization, and the
    `client_request_id` mint work. Read-only + construct — writes nothing (the
    `propose_transaction` write-invariant test depends on that staying true).

    Behavior contract (identical to the logic that was inline in
    `propose_transaction`):

      * `date` None → filled with `user_local_today(user.jwt)` (the caller's
        local calendar date, resolved under RLS in `users_meta.timezone`, UTC
        fallback). A supplied `date` is used verbatim. The default path never
        routes the date through an LLM (the fix for the "wrong date when I
        don't say one" class).
      * `category` supplied → used as-is with `gemini_suggestion=None` (no
        Gemini baseline to learn against). `category` None →
        `categorize(merchant, user)`; on success set both `category` and
        `gemini_suggestion` to the result (they start equal and diverge only
        when the user edits on the parse card — that divergence is the signal
        the confirm route's `merchant_category` upsert consumes). On
        `GeminiError`, fall back to `category="Other"`, `gemini_suggestion=None`
        (the categorize call already wrote its own `ai_call_log` row).
      * `card_id` is already resolved by the caller — chat resolves the short
        `card_ref` handle to a UUID via `_resolve_proposal_card`; the receipt
        path has no card and passes None. An unresolved/absent card stays None
        so the parse card prompts the user to pick.
      * `source` is `"nlp"` (chat) or `"receipt_photo"` (receipts). It is
        enum-constrained on the model.
      * `client_request_id` is minted fresh so every proposal is idempotent at
        `POST /transactions/confirm`.
    """
    resolved_date = date or user_local_today(user.jwt)

    if category is not None:
        resolved_category = category
        gemini_suggestion: str | None = None
    else:
        try:
            suggestion = categorize(merchant, user)
            resolved_category = suggestion.category
            gemini_suggestion = suggestion.category
        except GeminiError:
            resolved_category = "Other"
            gemini_suggestion = None

    return TransactionProposal(
        merchant=merchant,
        amount=amount,
        date=resolved_date,
        card_id=card_id,
        category=resolved_category,
        notes=notes,
        gemini_suggestion=gemini_suggestion,
        client_request_id=uuid4(),
        source=source,
    )
