"""Subscription REST endpoints — Day 19.

Confirm (from chat parse card), list, PATCH (edit fields + status
transitions), DELETE (soft cancel). No `POST /subscriptions` — user-
initiated creates flow through chat → `propose_subscription` → confirm
(CLAUDE.md invariant 8). `frequency` and `start_date` are immutable post-
create (DESIGN.md §8.3) — PATCH rejects them at the model layer via
`extra='forbid'` on `SubscriptionPatchRequest`.

All reads and writes go through `supabase_for_user(user.jwt)` so RLS fires
on every query. The service role is never used here (invariant 1).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.models.subscriptions import (
    SubscriptionConfirmRequest,
    SubscriptionListResponse,
    SubscriptionPatchRequest,
    SubscriptionRow,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("/confirm", response_model=SubscriptionRow)
def confirm_subscription(
    proposal: SubscriptionConfirmRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> SubscriptionRow:
    """Commit a subscription proposal to the user's tracker.

    Request body:
        SubscriptionProposal-shaped — name, amount, frequency, start_date,
        next_billing_date (already forward-only-clamped by the tool),
        category, optional card_id, client_request_id.

    Response:
        The created SubscriptionRow.

    Idempotency: if a row already exists for `(user_id, client_request_id)`,
    return that row instead of inserting. Same shape as Day 5's transactions
    confirm — the Day 15 offline-queue drain may retry the same confirm
    payload after a lost response, and without this short-circuit pg_cron
    would auto-log duplicate transactions every billing cycle.

    Card ownership: when `card_id` is set, validate it resolves to one of
    the user's active cards. Skip when `card_id is None` (cardless ACH
    subscriptions — DESIGN.md §8.3).

    Category: validated by `SubscriptionConfirmRequest` to be in
    `ALLOWED_CATEGORIES`.
    """
    existing = _load_existing_by_client_request_id(user, proposal.client_request_id)
    if existing is not None:
        return existing

    if proposal.card_id is not None:
        _assert_card_owned(user, proposal.card_id)

    client = supabase_for_user(user.jwt)
    insert_row: dict[str, object] = {
        "user_id": str(user.user_id),
        "name": proposal.name,
        "amount": str(proposal.amount),
        "frequency": proposal.frequency,
        "start_date": proposal.start_date.isoformat(),
        "next_billing_date": proposal.next_billing_date.isoformat(),
        "category": proposal.category,
        "client_request_id": str(proposal.client_request_id),
    }
    if proposal.card_id is not None:
        insert_row["card_id"] = str(proposal.card_id)

    try:
        ins = client.table("subscriptions").insert(insert_row).execute()
    except Exception:
        # Race: a concurrent request committed the same client_request_id
        # between our preflight and the insert. Re-read — if found, return
        # it; otherwise the error is real and should propagate.
        retry = _load_existing_by_client_request_id(user, proposal.client_request_id)
        if retry is not None:
            return retry
        raise

    return SubscriptionRow.model_validate(ins.data[0])


@router.get("", response_model=SubscriptionListResponse)
def get_subscriptions(
    user: AuthedUser = Depends(get_current_user_with_device),
    status_filter: str | None = Query(
        default="active",
        alias="status",
        description=(
            "Filter by lifecycle: 'active' (default), 'paused', 'cancelled', "
            "or 'all' to return every row. Cancelled rows stay accessible "
            "via 'all' for the chat-rehydrate and audit paths."
        ),
    ),
    include_card_af: bool = Query(
        default=False,
        description=(
            "When False (default), card annual-fee companion subscriptions "
            "are hidden from the response. AF rows are conceptually a "
            "card consequence, not a user-tracked recurring charge — the "
            "/subscriptions page surfaces user subscriptions only, while "
            "the cards-list AF chip queries this endpoint with "
            "?include_card_af=true. DESIGN.md §6.5 / §8.3."
        ),
    ),
) -> SubscriptionListResponse:
    """List the user's subscriptions, ordered by `next_billing_date`.

    Default returns only `status='active'` rows — that's what the
    `/subscriptions` page and the auto-logger care about. Pass
    `?status=paused` to surface paused-by-card-soft-delete rows for the
    needs-new-card banner, `?status=cancelled` for the history view, or
    `?status=all` to merge both.

    AF rows (Day 19b companion subscriptions) are hidden by default —
    pass `?include_card_af=true` to surface them. Recognition uses the
    same triple as the soft-delete cascade: `name LIKE '% annual fee'`
    + `category='Memberships'` + `frequency='annual'`. Filter is
    applied in Python after fetching — subscriptions are bounded to
    ~tens per user, so the cost is trivial and the SQL stays simple.

    No pagination — subscriptions are bounded to ~tens per user at v1 scale.
    """
    if status_filter not in {"active", "paused", "cancelled", "all", None}:
        raise _domain_error(
            "invalid_status",
            "status must be one of: active, paused, cancelled, all",
        )

    client = supabase_for_user(user.jwt)
    query = (
        client.table("subscriptions")
        .select("*")
        .order("next_billing_date", desc=False)
    )
    if status_filter and status_filter != "all":
        query = query.eq("status", status_filter)
    resp = query.execute()
    rows = resp.data or []
    if not include_card_af:
        rows = [row for row in rows if not _is_card_af_row(row)]
    return SubscriptionListResponse(
        items=[SubscriptionRow.model_validate(row) for row in rows],
    )


@router.patch("/{subscription_id}", response_model=SubscriptionRow)
def patch_subscription(
    subscription_id: UUID,
    patch: SubscriptionPatchRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> SubscriptionRow:
    """Edit a subscription's amount, category, name, card, or status.

    Rejected fields (immutability rule — §8.3): `frequency`, `start_date`.
    `extra='forbid'` on `SubscriptionPatchRequest` causes the body to 422
    if either is present. The UI hint "cancel and re-add to change billing
    cadence" lives in the frontend; the API contract is the structural
    rejection.

    Card reassignment: PATCHing `card_id` re-points an existing subscription
    at a different card (or to `null` for cardless ACH). Used by the
    needs-new-card banner that fires when a card is soft-deleted and its
    regular subscriptions flip to `paused` (DESIGN.md §8.3 split-cascade
    rule). Re-runs the ownership check on the new id.

    Status transitions: `active ↔ paused`; `active → cancelled` is also
    allowed via PATCH but normally goes through DELETE.
    """
    client = supabase_for_user(user.jwt)

    current_resp = (
        client.table("subscriptions")
        .select("*")
        .eq("id", str(subscription_id))
        .execute()
    )
    if not current_resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    current = current_resp.data[0]

    provided = patch.model_fields_set

    # `cancelled` is terminal per the §8.3 cancel/re-add doctrine.
    # PATCHing any field on a cancelled row — especially `status` —
    # would let a client reactivate it, and the pg_cron auto-logger
    # would start firing on a subscription the user thought was gone.
    # The supported flow is "create a fresh subscription via chat"
    # (which mints a new client_request_id and a new row).
    if current["status"] == "cancelled":
        raise _domain_error(
            "terminal_status",
            "cancelled subscriptions are terminal; create a new "
            "subscription via chat instead of reactivating the old one",
        )

    # NOT NULL columns: reject explicit null at the API boundary rather
    # than letting Postgres raise a cryptic constraint error.
    for required in ("name", "amount", "category", "status"):
        if required in provided and getattr(patch, required) is None:
            raise _domain_error(
                "null_not_allowed",
                f"{required} cannot be null — this column is NOT NULL",
            )

    if patch.card_id is not None:
        _assert_card_owned(user, patch.card_id)

    # Resume guard. When the user soft-deleted a card, the §8.3 split-
    # cascade flipped its regular subscriptions to `paused` but left
    # `card_id` pointing at the now-deleted card. Without this check, a
    # bare `{"status": "active"}` PATCH would re-enable the subscription
    # and pg_cron would start auto-logging onto the closed card. We
    # require the user to either reassign `card_id` (via the same
    # PATCH) or clear it to NULL (bank ACH) before reactivating.
    if "status" in provided and patch.status == "active":
        # Effective card_id: patch's value if the request set one,
        # else the current row's. Includes the explicit-null case so
        # clearing to ACH is a valid resume path.
        effective_card_id: str | None
        if "card_id" in provided:
            effective_card_id = (
                str(patch.card_id) if patch.card_id is not None else None
            )
        else:
            effective_card_id = current.get("card_id")
        if effective_card_id is not None:
            _assert_card_active(user, UUID(effective_card_id))

    update: dict[str, object | None] = {}
    if "name" in provided:
        update["name"] = patch.name
    if "amount" in provided:
        update["amount"] = str(patch.amount) if patch.amount is not None else None
    if "category" in provided:
        update["category"] = patch.category
    if "card_id" in provided:
        # Explicit null clears the FK (cardless reassignment); a UUID
        # re-points to another of the user's active cards.
        update["card_id"] = str(patch.card_id) if patch.card_id is not None else None
    if "status" in provided:
        update["status"] = patch.status

    if not update:
        return SubscriptionRow.model_validate(current)

    resp = (
        client.table("subscriptions")
        .update(update)
        .eq("id", str(subscription_id))
        # DB-side terminal guard. The pre-read check above runs on a
        # snapshot — the row can be cancelled between the SELECT and this
        # UPDATE (concurrent DELETE from another tab, offline-queue
        # interleaving), and a PATCH carrying {"status": "active"} would
        # silently reactivate it, putting the pg_cron auto-logger back in
        # business on a subscription the user thinks is gone. Same
        # between-the-queries window the transactions PATCH closes with
        # .eq("status", "active").
        .neq("status", "cancelled")
        .execute()
    )
    if not resp.data:
        # RLS, the row vanished, or it was cancelled between the two
        # queries. The pre-read proved it existed and was non-terminal a
        # moment ago, so surface the race as the same terminal_status
        # error a straight PATCH-on-cancelled gets.
        raise _domain_error(
            "terminal_status",
            "cancelled subscriptions are terminal; create a new "
            "subscription via chat instead of reactivating the old one",
        )
    return SubscriptionRow.model_validate(resp.data[0])


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(
    subscription_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Soft cancel: `status='cancelled'`.

    Subscriptions use `cancelled` (not `deleted`) as their terminal state —
    the §8 status-column doctrine treats this as the equivalent tombstone.
    The row stays in the table so historical auto-logged transactions
    retain their `subscription_id` link (§8.3 cancel/re-add doctrine).

    Idempotent: re-DELETE on an already-cancelled row is a no-op.
    """
    client = supabase_for_user(user.jwt)
    client.table("subscriptions").update({"status": "cancelled"}).eq(
        "id", str(subscription_id)
    ).neq("status", "cancelled").execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _domain_error(code: str, message: str) -> HTTPException:
    """422 with a structured detail. Mirrors the transactions confirm
    helper so the frontend's error-shape contract is uniform across
    ledger-adjacent endpoints."""
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _assert_card_active(user: AuthedUser, card_id: UUID) -> None:
    """Reject resume when the backing card has been soft-deleted.

    Distinct from `_assert_card_owned` because the user-facing error
    is different — the card *exists*, the user owns it, but it's
    been closed. Surfaces a `card_deleted` code so the frontend can
    render "this subscription's card was deleted — pick a new card
    or switch to ACH before resuming" instead of the generic
    "invalid card" copy. Same RLS-scoped read; an unknown / cross-
    tenant `card_id` still 422s but with the broader code.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("cards")
        .select("id, status")
        .eq("id", str(card_id))
        .execute()
    )
    if not resp.data:
        raise _domain_error(
            "invalid_card",
            "card_id does not resolve to one of your cards",
        )
    if resp.data[0].get("status") != "active":
        raise _domain_error(
            "card_deleted",
            "this subscription's card was deleted — pick a new card "
            "or clear `card_id` to bank ACH before resuming",
        )


def _assert_card_owned(user: AuthedUser, card_id: UUID) -> None:
    """Require `card_id` to resolve to an active card owned by the caller.

    RLS on `cards` returns empty for another user's card id; `status='active'`
    additionally filters out soft-deleted cards. Absent rows here mean any
    of: non-existent id, cross-tenant id, or deleted card — all fail the
    same way so a probing client can't enumerate ids or lifecycle. Same
    shape as `app/routes/transactions.py::_assert_card_owned`.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("cards")
        .select("id")
        .eq("id", str(card_id))
        .eq("status", "active")
        .execute()
    )
    if not resp.data:
        raise _domain_error(
            "invalid_card",
            "card_id does not resolve to one of your cards",
        )


def _is_card_af_row(row: dict) -> bool:
    """Match the Day 19b AF dual-write's shape.

    Three-field triple — the same recognition heuristic the soft-delete
    cascade in `app/routes/cards.py::delete_card` uses. Keeping the
    rule in one logical place would be nicer; for now it's duplicated
    by intent (both call sites are 3 lines) and tied together by the
    Day 19b name-template contract. If a future migration adds a
    `subscription_kind` enum column to subscriptions, both sites
    collapse onto that.
    """
    return (
        isinstance(row.get("name"), str)
        and row["name"].endswith(" annual fee")
        and row.get("category") == "Memberships"
        and row.get("frequency") == "annual"
    )


def _load_existing_by_client_request_id(
    user: AuthedUser, client_request_id: UUID
) -> SubscriptionRow | None:
    """Return the row for this `client_request_id`, or None.

    Scoped to non-cancelled rows so a replay of a confirm payload after
    the user cancelled the prior subscription creates a fresh active row
    (matches the §8.3 cancel/re-add doctrine). The Day 15 partial unique
    index makes at most one row eligible.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("subscriptions")
        .select("*")
        .eq("client_request_id", str(client_request_id))
        .neq("status", "cancelled")
        .execute()
    )
    if resp.data:
        return SubscriptionRow.model_validate(resp.data[0])
    return None
