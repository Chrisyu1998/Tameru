"""Transaction REST endpoints — Day 5.

Confirm (from chat parse card), list + detail (list UX + agent tool), PATCH
(edit sheet), DELETE (edit sheet / swipe). No `POST /transactions` — user-
initiated creates flow through chat → `propose_transaction` → confirm
(CLAUDE.md invariant 8).

All reads and writes go through `supabase_for_user(user.jwt)` so RLS fires on
every query. The service role is never used here (invariant 1).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.models.transactions import (
    DEFAULT_LIMIT,
    TransactionConfirmRequest,
    TransactionConfirmResponse,
    TransactionFilters,
    TransactionListResponse,
    TransactionPatchRequest,
    TransactionRow,
)
from app.services.transactions import list_transactions
from app.util.merchant import normalize_merchant

router = APIRouter(prefix="/transactions", tags=["transactions"])

# One-day slack on `date` upper bound: the client's local midnight may be up
# to ~24h ahead of server UTC. Anything further in the future is nonsense for
# a chat-typed transaction and is rejected; pg_cron writes future-dated auto-
# logged rows at the SQL layer, bypassing this validation.
_DATE_FUTURE_SLACK = timedelta(days=1)


@router.post("/confirm", response_model=TransactionConfirmResponse)
def confirm_transaction(
    proposal: TransactionConfirmRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> TransactionConfirmResponse:
    # Preflight — if we've already committed this client_request_id, return
    # the prior row untouched. `insight` stays None even after Day 13 wires
    # in entry_moment_insight(): the user either saw the insight on the
    # first confirm or has moved past it; re-firing is worse than silence
    # (DESIGN.md §6.2 step 7).
    """Provide confirm transaction."""
    existing = _load_existing_by_client_request_id(user, proposal.client_request_id)
    if existing is not None:
        return TransactionConfirmResponse(transaction=existing, insight=None)

    _assert_date_within_bounds(proposal.date)
    if proposal.card_id is not None:
        _assert_card_owned(user, proposal.card_id)

    client = supabase_for_user(user.jwt)
    insert_row: dict[str, object] = {
        "user_id": str(user.user_id),
        "merchant": proposal.merchant,
        "amount": str(proposal.amount),
        "date": proposal.date.isoformat(),
        "category": proposal.category,
        # Server-hardcoded per Day 5 prompt — the API body does not carry
        # a `source` field. CSV / pg_cron use their own values at the SQL
        # layer.
        "source": "nlp",
        "client_request_id": str(proposal.client_request_id),
    }
    if proposal.card_id is not None:
        insert_row["card_id"] = str(proposal.card_id)
    if proposal.gemini_suggestion is not None:
        insert_row["gemini_suggestion"] = proposal.gemini_suggestion
    if proposal.notes is not None:
        insert_row["notes"] = proposal.notes

    try:
        ins = client.table("transactions").insert(insert_row).execute()
    except Exception:
        # Race: a concurrent request committed the same client_request_id
        # between our preflight and the insert. Re-read — if found, return
        # it; otherwise the error is real and should propagate.
        retry = _load_existing_by_client_request_id(user, proposal.client_request_id)
        if retry is not None:
            return TransactionConfirmResponse(transaction=retry, insight=None)
        raise

    row = TransactionRow.model_validate(ins.data[0])

    # Confirm-on-override upsert (§8.4 site 1). Only fires when Gemini
    # actually proposed something different — a bare-missing suggestion
    # (None) is treated as "no baseline, don't pollute the cache."
    if (
        proposal.gemini_suggestion is not None
        and proposal.category != proposal.gemini_suggestion
    ):
        _upsert_merchant_correction(user, proposal.merchant, proposal.category)

    # Day 13 replaces `None` with entry_moment_insight(user, row).
    return TransactionConfirmResponse(transaction=row, insight=None)


@router.get("", response_model=TransactionListResponse)
def get_transactions(
    user: AuthedUser = Depends(get_current_user_with_device),
    card_id: UUID | None = Query(default=None),
    category: str | None = Query(default=None),
    merchant_contains: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    amount_min: float | None = Query(default=None),
    amount_max: float | None = Query(default=None),
    # No upper bound on `limit` at the HTTP layer — the service clamps
    # silently at MAX_LIMIT. Callers that pass a huge value get the clamp,
    # not a 422, matching the Day 5 prompt's "clamps silently" contract.
    limit: int = Query(default=DEFAULT_LIMIT, ge=1),
    offset: int = Query(default=0, ge=0),
) -> TransactionListResponse:
    """Provide get transactions."""
    filters = TransactionFilters(
        card_id=card_id,
        category=category,
        merchant_contains=merchant_contains,
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        limit=limit,
        offset=offset,
    )
    return list_transactions(user, filters)


@router.get("/{transaction_id}", response_model=TransactionRow)
def get_transaction(
    transaction_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> TransactionRow:
    """Provide get transaction."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("transactions")
        .select("*")
        .eq("id", str(transaction_id))
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return TransactionRow.model_validate(resp.data[0])


@router.patch("/{transaction_id}", response_model=TransactionRow)
def patch_transaction(
    transaction_id: UUID,
    patch: TransactionPatchRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> TransactionRow:
    """Provide patch transaction."""
    client = supabase_for_user(user.jwt)

    # Load the existing row first — both for the 404 path and so we can
    # compare category and pick the right merchant for the §8.4 upsert.
    current_resp = (
        client.table("transactions")
        .select("*")
        .eq("id", str(transaction_id))
        .execute()
    )
    if not current_resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    current = current_resp.data[0]

    # `model_fields_set` distinguishes "client sent this key" from "client
    # omitted this key," which is the difference between "clear this field"
    # and "leave this field alone" on a PATCH. `patch.card_id is None` alone
    # cannot tell the two cases apart — both look identical after Pydantic
    # applies the default.
    provided = patch.model_fields_set

    # NOT NULL columns (transactions.sql): reject explicit null at the API
    # boundary rather than letting Postgres raise a cryptic constraint
    # error. The Pydantic type is `T | None` so the model accepts the shape;
    # the semantic rule lives here.
    for required in ("merchant", "amount", "date", "category"):
        if required in provided and getattr(patch, required) is None:
            raise _domain_error(
                "null_not_allowed",
                f"{required} cannot be null — this column is NOT NULL",
            )

    if patch.date is not None:
        _assert_date_within_bounds(patch.date)
    if patch.card_id is not None:
        _assert_card_owned(user, patch.card_id)

    # Build a fields dict from only the keys the client actually sent. For
    # nullable columns (card_id, notes), explicit null passes through as
    # SQL NULL, letting users clear the FK or wipe their notes.
    update: dict[str, object | None] = {}
    if "merchant" in provided:
        update["merchant"] = patch.merchant
    if "amount" in provided:
        update["amount"] = str(patch.amount) if patch.amount is not None else None
    if "date" in provided:
        update["date"] = patch.date.isoformat() if patch.date is not None else None
    if "card_id" in provided:
        update["card_id"] = str(patch.card_id) if patch.card_id is not None else None
    if "category" in provided:
        update["category"] = patch.category
    if "notes" in provided:
        update["notes"] = patch.notes

    if not update:
        # Nothing to do — return the row as-is. Day 15's edit sheet
        # disables Save until a field differs, so this is mostly a
        # defensive branch for API clients.
        return TransactionRow.model_validate(current)

    resp = (
        client.table("transactions")
        .update(update)
        .eq("id", str(transaction_id))
        .execute()
    )
    if not resp.data:
        # RLS or row vanished between the two queries. Treat as 404.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    updated = resp.data[0]

    # §8.4 upsert site 2 — category changed via edit. Keyed on the NEW
    # merchant (if the PATCH changed it) so "I fixed the spelling AND the
    # category" correctly seeds the cache under the canonical name. A
    # merchant-only PATCH doesn't touch this table.
    if patch.category is not None and patch.category != current["category"]:
        final_merchant = (
            patch.merchant if patch.merchant is not None else current["merchant"]
        )
        _upsert_merchant_correction(user, final_merchant, patch.category)

    return TransactionRow.model_validate(updated)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Provide delete transaction."""
    client = supabase_for_user(user.jwt)
    client.table("transactions").delete().eq("id", str(transaction_id)).execute()
    # RLS makes "delete nonexistent" and "delete someone else's row" both
    # be no-ops — we return 204 in either case rather than leaking which
    # ids exist by 404-ing on one branch and 204-ing on the other.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _domain_error(code: str, message: str) -> HTTPException:
    # 422 per Day 5 prompt; the name `HTTP_422_UNPROCESSABLE_ENTITY` was
    # renamed to `HTTP_422_UNPROCESSABLE_CONTENT` in Starlette 0.40. Hard-
    # code 422 so we stay on one name regardless of which lands first.
    """Support domain error."""
    return HTTPException(status_code=422, detail={"code": code, "message": message})

def _assert_card_owned(user: AuthedUser, card_id: UUID) -> None:
    """RLS on `cards` returns empty for another user's card id; an absent
    row here means either a non-existent id or a cross-tenant id. Both
    should fail the same way — the error message doesn't distinguish so a
    probing client can't enumerate other users' card ids."""
    client = supabase_for_user(user.jwt)
    resp = client.table("cards").select("id").eq("id", str(card_id)).execute()
    if not resp.data:
        raise _domain_error(
            "invalid_card",
            "card_id does not resolve to one of your cards",
        )

def _assert_date_within_bounds(d: date) -> None:
    """Support assert date within bounds."""
    if d > date.today() + _DATE_FUTURE_SLACK:
        raise _domain_error(
            "invalid_date",
            "date cannot be more than one day in the future",
        )

def _load_existing_by_client_request_id(
    user: AuthedUser, client_request_id: UUID
) -> TransactionRow | None:
    """Support load existing by client request id."""
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("transactions")
        .select("*")
        .eq("client_request_id", str(client_request_id))
        .execute()
    )
    if resp.data:
        return TransactionRow.model_validate(resp.data[0])
    return None

def _upsert_merchant_correction(
    user: AuthedUser, merchant: str, category: str
) -> None:
    """One of two sites — the other is PATCH. Keep the upsert shape
    identical so the "most recent correction wins" contract (§8.4) is
    satisfied regardless of which surface recorded it."""
    client = supabase_for_user(user.jwt)
    client.table("merchant_category").upsert(
        {
            "user_id": str(user.user_id),
            "merchant": normalize_merchant(merchant),
            "category": category,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id,merchant",
    ).execute()
