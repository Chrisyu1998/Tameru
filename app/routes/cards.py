"""Card REST endpoints — Day 14.

Lookup (drives the parse-card preview), confirm (the only commit path),
list, edit, soft-delete. There is no `POST /cards` — every commit goes
through `/cards/confirm` after a proposal the user saw, matching the
propose-then-confirm contract for ledger-adjacent rows (CLAUDE.md
invariant 8).

The lookup endpoint is also called by the `propose_card` agent tool
internals (app/agent/tools.py) — both surfaces share one integration
module so the same web_search query, allowlist, and ai_call_log shape
power both entry points.

Soft-delete + re-add semantics: see DESIGN.md §8.1. Inactive rows are
never revived — DELETE flips `active=false` and stamps `deactivated_at`,
and a new insert with the same `(network, last_four)` creates a fresh
row with a new `card_id`. Old transactions stay linked to the old row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user
from app.integrations.card_lookup import lookup_card
from app.models.cards import (
    ActiveCardExistsDetail,
    CardConfirmRequest,
    CardListResponse,
    CardLookupRequest,
    CardLookupResponse,
    CardPatchRequest,
    CardRow,
)

router = APIRouter(prefix="/cards", tags=["cards"])


@router.post("/lookup", response_model=CardLookupResponse)
def post_card_lookup(
    body: CardLookupRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardLookupResponse:
    """Run the Claude web_search-backed multiplier lookup.

    Request body:
        {"name": "Chase Sapphire Reserve"}

    Response:
        {"name": "Chase Sapphire Reserve", "lookup": {<CardLookupResult>}}

    The lookup itself never raises — failures land as `needs_manual=True`
    on the result so the UI can render the manual-fill form. ai_call_log
    captures the outcome (provider="anthropic", task_type="card_lookup",
    invariant 14) regardless of success.
    """
    result = lookup_card(body.name, user)
    return CardLookupResponse(name=body.name, lookup=result)


@router.post("/confirm", response_model=CardRow)
def post_card_confirm(
    proposal: CardConfirmRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardRow:
    """Commit a card proposal to the user's wallet.

    Request body:
        CardProposal-shaped — network, last_four, name, issuer, program,
        multipliers, annual_fee, source_urls, alias?.

    Response:
        The created CardRow (201 implied — FastAPI returns 200 by default;
        the response_model is the new row either way, matching the
        transactions confirm shape from Day 5).

    409 collision flow (DESIGN.md §8.1, §6.1):
        - If a row already exists with the same (user_id, network,
          last_four) AND active=true, the `cards_active_identity_uniq`
          partial unique index fires. We catch the unique violation and
          return HTTP 409 with `code=active_card_exists` plus the
          existing card's id, name, and last_four so the frontend can
          render the "edit it instead?" affordance.
        - If only an INACTIVE row matches, the partial index does NOT
          fire and the insert succeeds — a new row with a new `card_id`
          is created. Old transactions stay linked to the previous
          inactive row.

    No `client_request_id` idempotency here. Cards are ≤10/user lifetime;
    the cost of a duplicate (delete and re-add) is recoverable in one tap.
    See Day 14 prompt and DESIGN.md §8.1 for the proportionate-cost
    reasoning.
    """
    client = supabase_for_user(user.jwt)

    insert_row: dict[str, object] = {
        "user_id": str(user.user_id),
        "name": proposal.name,
        "issuer": proposal.issuer,
        "network": proposal.network,
        "program": proposal.program,
        "multipliers": proposal.multipliers,
        "last_four": proposal.last_four,
        "source_urls": proposal.source_urls,
        # `active` and `created_at` come from column defaults; do not
        # set them here so a future default change doesn't silently
        # diverge from the migration.
    }
    if proposal.annual_fee is not None:
        insert_row["annual_fee"] = str(proposal.annual_fee)
    if proposal.color is not None:
        insert_row["color"] = proposal.color

    try:
        resp = client.table("cards").insert(insert_row).execute()
    except Exception as exc:
        # Unique-violation taxonomy: the partial index `cards_active_identity_uniq`
        # is the only column-level constraint a propose-confirm proposal can trip.
        # PostgREST surfaces the Postgres SQLSTATE 23505 message in a stable way;
        # we string-match the index name for forward-compat across SDK versions.
        message = str(exc)
        if "cards_active_identity_uniq" in message or "duplicate key" in message:
            raise _collision_409(client, proposal.network, proposal.last_four) from exc
        raise

    return CardRow.model_validate(resp.data[0])


@router.get("", response_model=CardListResponse)
def get_cards(
    user: AuthedUser = Depends(get_current_user_with_device),
    include_inactive: bool = Query(
        default=False,
        description=(
            "When True, include soft-deleted cards. Used by the spending-"
            "breakdown filter (DESIGN.md §8.1 frontend filter rules)."
        ),
    ),
) -> CardListResponse:
    """List the user's cards.

    Default: active cards only — the cards-page list (UX frame 18) and
    the agent's `get_cards` tool both want only the live wallet.

    `include_inactive=true`: returns active + inactive in one list. The
    spending-breakdown filter renders inactive rows with a "closed
    {MMM YYYY}" suffix in muted color (DESIGN.md §8.1 Rule 3).

    No pagination — cards are bounded to ~10 per user lifetime.
    """
    client = supabase_for_user(user.jwt)
    query = client.table("cards").select("*").order("created_at", desc=False)
    if not include_inactive:
        query = query.eq("active", True)
    resp = query.execute()
    rows = resp.data or []
    return CardListResponse(items=[CardRow.model_validate(row) for row in rows])


@router.patch("/{card_id}", response_model=CardRow)
def patch_card(
    card_id: UUID,
    patch: CardPatchRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> CardRow:
    """Edit a card's name, program, multipliers, annual_fee, or color.

    Identity fields (`network`, `last_four`, `issuer`) are NOT patchable —
    those represent who the card *is*. To "change" identity, the user
    deletes the card and re-adds it via chat (new propose → new confirm).
    """
    client = supabase_for_user(user.jwt)

    current_resp = (
        client.table("cards")
        .select("*")
        .eq("id", str(card_id))
        .execute()
    )
    if not current_resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    provided = patch.model_fields_set
    update: dict[str, object | None] = {}
    if "name" in provided:
        update["name"] = patch.name
    if "program" in provided:
        update["program"] = patch.program
    if "multipliers" in provided:
        update["multipliers"] = patch.multipliers
    if "annual_fee" in provided:
        update["annual_fee"] = (
            str(patch.annual_fee) if patch.annual_fee is not None else None
        )
    if "color" in provided:
        update["color"] = patch.color

    if not update:
        return CardRow.model_validate(current_resp.data[0])

    resp = (
        client.table("cards")
        .update(update)
        .eq("id", str(card_id))
        .execute()
    )
    if not resp.data:
        # RLS or the row vanished between the two queries.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CardRow.model_validate(resp.data[0])


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card(
    card_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Soft-delete: `active=false` + `deactivated_at=now()`.

    The row stays in the table so historical transactions linked via
    `transactions.card_id` keep their card snapshot. The partial unique
    index frees up the `(user_id, network, last_four)` slot so the user
    can re-add the same card if they want — a new row gets a fresh
    `card_id` (DESIGN.md §8.1 soft-delete / re-add semantics).

    RLS makes "delete nonexistent" and "delete someone else's card"
    indistinguishable from the caller's seat — both return 204 without
    a write. Matches the transactions DELETE behavior from Day 5.
    """
    client = supabase_for_user(user.jwt)
    client.table("cards").update(
        {
            "active": False,
            "deactivated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", str(card_id)).eq("active", True).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _collision_409(
    client, network: str, last_four: str
) -> HTTPException:
    """Resolve the colliding active row and build the 409 payload.

    Called from the unique-violation branch of POST /cards/confirm. RLS
    scopes the read so we can only resolve our own collisions — exactly
    the row the frontend will edit.

    If for some reason the colliding row can't be re-read (race window
    where it was deleted between INSERT failure and SELECT), fall back
    to a 409 without an `existing_card_id` so the frontend at least
    surfaces a non-silent error.
    """
    try:
        lookup = (
            client.table("cards")
            .select("id, name, last_four")
            .eq("network", network)
            .eq("last_four", last_four)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        row = (lookup.data or [None])[0]
    except Exception:
        row = None

    if row is None:
        # Race window — collision detected but we can't surface details.
        # Return a minimal 409 rather than swallowing.
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "active_card_exists",
                "message": (
                    f"a card with network={network} ending {last_four} "
                    "is already active"
                ),
            },
        )

    detail = ActiveCardExistsDetail(
        message=(
            f"you already have {row['name']} ending {row['last_four']}. "
            "edit that one instead."
        ),
        existing_card_id=UUID(row["id"]),
        existing_card_name=row["name"],
        existing_card_last_four=row.get("last_four"),
    )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail.model_dump(mode="json"),
    )
