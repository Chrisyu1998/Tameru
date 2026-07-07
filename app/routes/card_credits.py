"""Card statement-credit REST endpoints — Phase 1 (DESIGN.md §6.7, §8.17).

Lookup (drives the propose-confirm checklist), confirm (the only commit path,
via the `card_credits_confirm` SECURITY INVOKER upsert RPC), list, edit
(set-used-amount / rename / re-amount / re-cadence / archive), and archive.

This is an auxiliary table, not a ledger table, so invariant 8 does not govern
it (same standing as goals / user_memory). The feature honors the spirit
anyway: the lookup path is propose-then-confirm and every mutation flows
through an explicit HTTP call under the user's JWT — never a `tool_use` write.

Credits are card consequences: a "credits" chip on the card row opens the
Credits page (`/cards/:cardId/credits`), the AF-chip sibling surface. There is
no `tool_use` write path and (Phase 1) no chat tool — Phase 2 adds a read-only
`get_card_credits`.
"""

from __future__ import annotations

import datetime as _dt
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.integrations.card_lookup import lookup_card_credits
from app.models.card_credits import (
    ApplyCreditUsageRequest,
    CardCreditHistoryResponse,
    CardCreditHistoryRow,
    CardCreditListResponse,
    CardCreditPatchRequest,
    CardCreditRow,
    CardCreditsConfirmRequest,
    CardCreditsLookupRequest,
    CardCreditsLookupResponse,
    CreditProposal,
)
from app.util.timezone import user_local_today

router = APIRouter(prefix="/card-credits", tags=["card-credits"])


@router.post("/lookup", response_model=CardCreditsLookupResponse)
def post_card_credits_lookup(
    body: CardCreditsLookupRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardCreditsLookupResponse:
    """Run the web_search-backed lookup of a card's recurring statement credits.

    Request body:
        {"card_id": "…uuid…"}

    Response:
        CardCreditsLookupResponse — the resolved card name plus a list of
        `CreditProposal` (each with a server-minted `client_request_id`,
        `verified_at`, and the shared `source_urls`). The client renders the
        propose-confirm checklist.

    Never a hard error on lookup failure: the integration mirrors
    `lookup_card`'s never-raises contract, so a provider/parse failure or a
    card with no documented credits returns HTTP 200 with an empty `credits`
    list and `needs_manual=True` — the UI then offers only the manual-add path.
    ai_call_log captures every outcome (task_type="credit_lookup", invariant 14).
    """
    client = supabase_for_user(user.jwt)
    card = _require_active_card(client, body.card_id)
    home_currency = _home_currency(client, user.user_id)

    result = lookup_card_credits(card["name"], user, home_currency=home_currency)

    verified_at = _dt.datetime.now(_dt.timezone.utc)
    proposals = [
        CreditProposal(
            card_id=body.card_id,
            name=c.name,
            amount=c.amount,
            cadence=c.cadence,
            merchant_hint=c.merchant_hint,
            source_urls=result.source_urls,
            verified_at=verified_at,
            client_request_id=uuid4(),
        )
        for c in result.credits
    ]
    return CardCreditsLookupResponse(
        card_id=body.card_id,
        card_name=card["name"],
        credits=proposals,
        source_urls=result.source_urls,
        needs_manual=result.needs_manual,
    )


@router.post("/confirm", response_model=CardCreditListResponse)
def post_card_credits_confirm(
    body: CardCreditsConfirmRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardCreditListResponse:
    """Commit a checklist of proposed credits.

    Request body:
        {"credits": [CreditProposal, ...]} — the (possibly edited) list the
        user checked. A manual add is a single-element list with a
        client-minted `client_request_id`, empty `source_urls`, null
        `verified_at`.

    Response:
        CardCreditListResponse — the rows that actually landed. Idempotent: a
        replay (same names on the same card) dedups on the
        `(card_id, lower(name))` partial index inside `card_credits_confirm`
        and returns fewer rows (or none). The write goes through the SECURITY
        INVOKER upsert RPC, which also seeds `current_period_start` /
        `next_reset_date` from `credit_period_bounds()` and drops any credit
        whose `card_id` is not the caller's active card.
    """
    client = supabase_for_user(user.jwt)
    p_rows = [_proposal_to_row(c) for c in body.credits]
    resp = client.rpc("card_credits_confirm", {"p_rows": p_rows}).execute()
    rows = resp.data or []
    return CardCreditListResponse(
        items=[CardCreditRow.model_validate(r) for r in rows]
    )


@router.get("", response_model=CardCreditListResponse)
def get_card_credits(
    card_id: UUID = Query(..., description="The card whose credits to list."),
    include_archived: bool = Query(
        default=False,
        description="When True, include archived (stopped-tracking) credits.",
    ),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardCreditListResponse:
    """List a card's statement credits (active by default).

    RLS scopes the read to the caller. `include_archived=true` also returns
    stopped-tracking rows so the Credits page can offer re-enable. No
    pagination — credits are bounded to a handful per card.
    """
    client = supabase_for_user(user.jwt)
    query = (
        client.table("card_credits")
        .select("*")
        .eq("card_id", str(card_id))
        .order("created_at", desc=False)
    )
    if not include_archived:
        query = query.eq("status", "active")
    rows = query.execute().data or []
    return CardCreditListResponse(
        items=[CardCreditRow.model_validate(r) for r in rows]
    )


@router.patch("/{credit_id}", response_model=CardCreditRow)
def patch_card_credit(
    credit_id: UUID,
    patch: CardCreditPatchRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardCreditRow:
    """Edit a credit: used_amount, name, amount, cadence, or status.

    `used_amount` is the set-used-amount action from the Credits page progress
    bar. A `cadence` change recomputes `current_period_start` /
    `next_reset_date` from `credit_period_bounds()` (in the user's local tz) so
    the reset schedule follows the new cadence — the period math stays in the
    single SQL source of truth. `status='archived'` is the "stop tracking"
    action (same effect as DELETE). Identity/period fields (`card_id`,
    `current_period_start`, `next_reset_date`, `client_request_id`) are not
    patchable.
    """
    client = supabase_for_user(user.jwt)

    current = (
        client.table("card_credits")
        .select("*")
        .eq("id", str(credit_id))
        .execute()
    )
    if not current.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    provided = patch.model_fields_set
    update: dict[str, object | None] = {}
    if "used_amount" in provided:
        update["used_amount"] = (
            str(patch.used_amount) if patch.used_amount is not None else None
        )
    if "name" in provided:
        update["name"] = patch.name
    if "amount" in provided:
        update["amount"] = str(patch.amount) if patch.amount is not None else None
    if "status" in provided:
        update["status"] = patch.status
    if "cadence" in provided and patch.cadence is not None:
        update["cadence"] = patch.cadence
        # Recompute the period bounds for the new cadence via the shared SQL
        # helper, anchored on the user's local today (same source of truth the
        # confirm seed and the reset advance use — no Python/SQL drift).
        today = user_local_today(user.jwt)
        bounds = client.rpc(
            "credit_period_bounds",
            {"p_cadence": patch.cadence, "p_on_date": today.isoformat()},
        ).execute()
        if bounds.data:
            update["current_period_start"] = bounds.data[0]["period_start"]
            update["next_reset_date"] = bounds.data[0]["next_reset"]

    if not update:
        return CardCreditRow.model_validate(current.data[0])

    try:
        resp = (
            client.table("card_credits")
            .update(update)
            .eq("id", str(credit_id))
            .execute()
        )
    except Exception as exc:
        # A rename that collides with another active credit of the same name
        # on the same card trips `card_credits_active_name_uniq`.
        if "card_credits_active_name_uniq" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "duplicate_credit_name",
                    "message": "a credit with that name already exists on this card",
                },
            ) from exc
        raise
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CardCreditRow.model_validate(resp.data[0])


@router.delete("/{credit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card_credit(
    credit_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Archive a credit ("stop tracking"). Never a hard DELETE.

    Flips `status='archived'` (§8 status-column doctrine). RLS scopes the write
    to the caller; an unknown id is a silent no-op (204 either way, so a
    probing client can't enumerate credit ids). Re-adding the same-named credit
    later is allowed because the `(card_id, lower(name))` unique index is
    partial on `status='active'`.
    """
    client = supabase_for_user(user.jwt)
    (
        client.table("card_credits")
        .update({"status": "archived"})
        .eq("id", str(credit_id))
        .eq("status", "active")
        .execute()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{credit_id}/apply", response_model=CardCreditRow)
def apply_credit_usage(
    credit_id: UUID,
    body: ApplyCreditUsageRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardCreditRow:
    """Count a matched transaction toward a credit — the Phase-2 ledger tap.

    Request body:
        {"transaction_id": "…uuid…"}

    Response:
        CardCreditRow — the updated credit (new `used_amount`).

    The `card_credit_apply_usage` RPC reads the transaction's amount + date
    itself under RLS (never a client-sent delta), then atomically increments
    `used_amount` clamped to `[0, allowance]`, guarded on same-card +
    `date >= current_period_start`. A single-statement update means concurrent
    bridge taps can't lose an update. An empty result — the credit or
    transaction isn't the caller's, they're on different cards, or the spend
    predates the current period — is a 409 (indistinguishable by design, so a
    probing client learns nothing).
    """
    client = supabase_for_user(user.jwt)
    resp = client.rpc(
        "card_credit_apply_usage",
        {
            "p_credit_id": str(credit_id),
            "p_transaction_id": str(body.transaction_id),
        },
    ).execute()
    rows = resp.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "credit_apply_failed",
                "message": "that transaction could not be counted toward this credit",
            },
        )
    return CardCreditRow.model_validate(rows[0])


@router.get("/{credit_id}/history", response_model=CardCreditHistoryResponse)
def get_card_credit_history(
    credit_id: UUID,
    limit: int = Query(
        default=8, ge=1, le=60, description="Max closed periods to return."
    ),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardCreditHistoryResponse:
    """List a credit's closed-period snapshots, newest first (§8.18).

    Powers the Credits page "last {period} you used $X". RLS scopes the read to
    the caller — a foreign `credit_id` returns empty rather than leaking. The
    table is written only by the `reset_card_credits()` sweep, so this is a pure
    read.
    """
    client = supabase_for_user(user.jwt)
    rows = (
        client.table("card_credit_history")
        .select("*")
        .eq("card_credit_id", str(credit_id))
        .order("period_start", desc=True)
        .limit(limit)
        .execute()
    ).data or []
    return CardCreditHistoryResponse(
        items=[CardCreditHistoryRow.model_validate(r) for r in rows]
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _proposal_to_row(c: CreditProposal) -> dict[str, object]:
    """Serialize a CreditProposal into the jsonb row shape the RPC parses.

    Decimal → str and datetime → isoformat because the payload is JSON-encoded
    on the wire to PostgREST; omitting a null amount lets the RPC's
    NULLIF(...,'') resolve it to SQL NULL.
    """
    row: dict[str, object] = {
        "card_id": str(c.card_id),
        "name": c.name,
        "cadence": c.cadence,
        "source_urls": c.source_urls,
        "client_request_id": str(c.client_request_id),
    }
    if c.amount is not None:
        row["amount"] = str(c.amount)
    if c.merchant_hint is not None:
        row["merchant_hint"] = c.merchant_hint
    if c.verified_at is not None:
        row["verified_at"] = c.verified_at.isoformat()
    return row


def _require_active_card(client, card_id: UUID) -> dict:
    """Resolve the caller's active card row or 404.

    RLS scopes the read to the caller, so a card_id owned by another user (or a
    deleted card) resolves to nothing → 404. Used by the lookup to get the card
    name for the web_search query and to reject credits on a card the user
    doesn't own.
    """
    resp = (
        client.table("cards")
        .select("id, name")
        .eq("id", str(card_id))
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return resp.data[0]


def _home_currency(client, user_id: UUID) -> str:
    """Read the user's immutable home_currency for the lookup amount prompt.

    Falls back to USD when no users_meta row exists yet. RLS scopes the read.
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
