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

Soft-delete + re-add semantics: see DESIGN.md §8.1. Deleted rows are
never revived — DELETE flips `status='deleted'` and stamps `deleted_at`,
and a new insert with the same `(issuer, last_four)` creates a fresh
row with a new `card_id`. Old transactions stay linked to the old row.
"""

from __future__ import annotations

from uuid import UUID, uuid4

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
        - If a row already exists with the same (user_id, issuer,
          last_four) AND status='active', the `cards_active_identity_uniq`
          partial unique index fires. We catch the unique violation and
          return HTTP 409 with `code=active_card_exists` plus the
          existing card's id, name, and last_four so the frontend can
          render the "edit it instead?" affordance.
        - If only a DELETED row matches, the partial index does NOT
          fire and the insert succeeds — a new row with a new `card_id`
          is created. Old transactions stay linked to the previous
          deleted row.
        - Two cards from DIFFERENT issuers with the same network and
          last_four (e.g. Chase Visa 1234 and Capital One Visa 1234)
          coexist freely under the issuer-keyed index. This was the
          bug the (network, last_four) index had — see migration
          20260516140000.

    `client_request_id` idempotency (Day 15 follow-up):
        Same crid → return the existing row (200), no duplicate insert,
        no 409. Mirrors `/transactions/confirm` idempotency. A network
        retry of the exact same proposal is harmless.

        This is NOT the structural dedup — the partial unique index on
        `(user_id, issuer, last_four) WHERE status = 'active'` still
        owns that. crid handles "same proposal posted twice"; the
        natural-key 409 handles "different proposals for the same
        physical card."
    """
    # `CardProposal.last_four` is nullable on the wire so the `propose_card`
    # tool can return a proposal mid-conversation before the user has typed
    # it. At commit time, the parse-card UI must have collected it — defend
    # against a forged client that POSTs without one. The DB column is
    # nullable too, but a missing last_four would make the partial unique
    # index treat the row as distinct from every other null-last_four row,
    # silently allowing duplicates per user. 422 here is the right boundary.
    if proposal.last_four is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "missing_last_four",
                "message": "last_four is required at commit time",
            },
        )

    client = supabase_for_user(user.jwt)

    # crid short-circuit: a same-crid replay returns the prior row. The
    # partial unique index `cards_active_client_request_id_unique`
    # guarantees at most one active row per (user_id, client_request_id),
    # so this lookup is safe — single-row or empty. RLS scopes it to the
    # caller's own rows. Soft-deleted-then-readded cards mint a fresh
    # crid (the propose-confirm cycle reruns), so a re-add never matches
    # the deleted row here.
    crid_lookup = (
        client.table("cards")
        .select("*")
        .eq("client_request_id", str(proposal.client_request_id))
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if crid_lookup.data:
        return CardRow.model_validate(crid_lookup.data[0])

    insert_row: dict[str, object] = {
        "user_id": str(user.user_id),
        "name": proposal.name,
        "issuer": proposal.issuer,
        "network": proposal.network,
        "program": proposal.program,
        "multipliers": proposal.multipliers,
        "last_four": proposal.last_four,
        "source_urls": proposal.source_urls,
        "client_request_id": str(proposal.client_request_id),
        # `status` defaults to 'active' and `created_at` to now(); do not
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
        # Unique-violation taxonomy: two partial indexes can fire here.
        # `cards_active_identity_uniq` is the natural-key dedup (same
        # physical card) — surfaces as 409 active_card_exists for the
        # frontend's "edit that one" affordance.
        # `cards_active_client_request_id_unique` should never fire
        # because the crid short-circuit above caught the only legitimate
        # replay path; if it does fire, treat it the same as a crid
        # replay (return the existing row).
        message = str(exc)
        if "cards_active_client_request_id_unique" in message:
            replay = (
                client.table("cards")
                .select("*")
                .eq("client_request_id", str(proposal.client_request_id))
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            if replay.data:
                return CardRow.model_validate(replay.data[0])
            # Genuinely impossible — fall through to the generic raise.
        if "cards_active_identity_uniq" in message or "duplicate key" in message:
            raise _collision_409(client, proposal.issuer, proposal.last_four) from exc
        raise

    new_card = CardRow.model_validate(resp.data[0])

    # Day 19b — AF dual-write. When the user supplied a renewal date AND
    # the card has a non-zero annual fee, create a companion subscriptions
    # row so the pg_cron auto-logger logs the AF on each anniversary.
    # The subscription has a freshly minted server-side `client_request_id`
    # so the partial unique index on `subscriptions (user_id,
    # client_request_id)` makes a retry of `/cards/confirm` (e.g. network
    # blip after the cards INSERT succeeded but before the response
    # reached the client) a no-op rather than racy. The cards crid
    # short-circuit above is the primary retry guard; this is
    # defense-in-depth.
    if (
        proposal.next_annual_fee_date is not None
        and proposal.annual_fee is not None
        and proposal.annual_fee > 0
    ):
        _insert_af_subscription(client, user, new_card, proposal)

    return new_card


@router.get("", response_model=CardListResponse)
def get_cards(
    user: AuthedUser = Depends(get_current_user_with_device),
    include_inactive: bool = Query(
        default=False,
        description=(
            "When True, include soft-deleted (status='deleted') cards. Used by "
            "the spending-breakdown filter (DESIGN.md §8.1 frontend filter rules)."
        ),
    ),
) -> CardListResponse:
    """List the user's cards.

    Default: active cards only — the cards-page list (UX frame 18) and
    the agent's `get_cards` tool both want only the live wallet.

    `include_inactive=true`: returns active + deleted in one list. The
    spending-breakdown filter renders deleted rows with a "closed
    {MMM YYYY}" suffix in muted color (DESIGN.md §8.1 Rule 3).

    No pagination — cards are bounded to ~10 per user lifetime.
    """
    client = supabase_for_user(user.jwt)
    query = client.table("cards").select("*").order("created_at", desc=False)
    if not include_inactive:
        query = query.eq("status", "active")
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
    """Soft-delete the card + cascade companion subscriptions, atomically.

    The row stays in the table so historical transactions linked via
    `transactions.card_id` keep their card snapshot. The partial unique
    index frees up the `(user_id, issuer, last_four)` slot so the user
    can re-add the same card if they want — a new row gets a fresh
    `card_id` (DESIGN.md §8.1 soft-delete / re-add semantics).

    The whole operation runs in a single SQL transaction via the
    `soft_delete_card(p_card_id UUID)` SECURITY DEFINER function
    (migration 20260518130300). That guarantees all three updates
    commit or none do — there is no window where the card is gone but
    the subscriptions weren't reassigned, or vice versa. The prior
    inline-UPDATE-chain version was idempotent on retry but had a
    visible inconsistent state if any pass failed.

    Cascade rules (DESIGN.md §8.3):

      - **Regular subscriptions** (Netflix, gym, ACH rent) → flip to
        `status='paused'`. The `/subscriptions` page surfaces a
        needs-new-card banner; the user reassigns `card_id` via PATCH
        and un-pauses. Pg_cron skips paused rows so nothing logs while
        the user decides.
      - **Card annual-fee subscriptions** → flip to `status='cancelled'`.
        The fee is bound to *this physical card*; there is no
        third-party recipient or other card to reassign to.

    AF subscriptions are recognised by the (`name LIKE '% annual fee'`,
    `category='Subscriptions'`, `frequency='annual'`) triple — the same
    shape Day 19b's `POST /cards/confirm` AF dual-write inserts.

    Security: the function is SECURITY DEFINER but every WHERE clause
    inside it is filtered by `auth.uid()`, which PostgREST populates
    from the caller's JWT. A user cannot soft-delete another user's
    card; an unknown id is silently a no-op (matches the prior
    behavior, which let RLS produce the same outcome). Both surfaces
    return 204 indistinguishably so a probing client can't enumerate
    card ids.
    """
    client = supabase_for_user(user.jwt)
    client.rpc("soft_delete_card", {"p_card_id": str(card_id)}).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _insert_af_subscription(
    client, user: AuthedUser, new_card: CardRow, proposal: CardConfirmRequest
) -> None:
    """Insert the companion AF subscription alongside a card confirm.

    Day 19b. Called only when `next_annual_fee_date` is set and
    `annual_fee > 0`. The subscription's shape matches what the Day 19
    soft-delete cascade looks for to flip it to 'cancelled':

      - `name = '{card_name} annual fee'`
      - `category = 'Subscriptions'`
      - `frequency = 'annual'`

    `start_date` and `next_billing_date` both land on the user-supplied
    renewal date; pg_cron's autolog_subscriptions() handles the rest on
    the anniversary. A fresh server-side `client_request_id` makes the
    insert idempotent under the subscriptions partial unique index — a
    `/cards/confirm` retry that re-enters this branch with a different
    `client_request_id` would create a duplicate, but the cards crid
    short-circuit on the caller side prevents that path from ever firing
    in normal flow.

    Best-effort: a unique-violation here (extremely unlikely — would
    require a race that bypassed the cards crid short-circuit) is
    swallowed so the card itself still surfaces to the user. The user
    can re-enter the AF date via chat if it didn't take.
    """
    try:
        client.table("subscriptions").insert(
            {
                "user_id": str(user.user_id),
                "card_id": str(new_card.id),
                "name": f"{proposal.name} annual fee",
                "amount": str(proposal.annual_fee),
                "frequency": "annual",
                "start_date": proposal.next_annual_fee_date.isoformat(),
                "next_billing_date": proposal.next_annual_fee_date.isoformat(),
                "category": "Subscriptions",
                "status": "active",
                "client_request_id": str(uuid4()),
            }
        ).execute()
    except Exception as exc:
        # Same-crid-replay impossible (fresh UUID). A legitimate failure
        # path here is the user re-confirming the same card after a
        # network blip — but the cards crid short-circuit at the top of
        # `post_card_confirm` already returned the prior CardRow before
        # we got here, so this branch only fires on truly unexpected
        # errors. Swallow rather than 500-ing on the card create.
        _ = exc  # explicit no-op; future log site if needed


def _collision_409(
    client, issuer: str, last_four: str
) -> HTTPException:
    """Resolve the colliding active row and build the 409 payload.

    Called from the unique-violation branch of POST /cards/confirm. RLS
    scopes the read so we can only resolve our own collisions — exactly
    the row the frontend will edit.

    Keyed on `issuer` + `last_four` because the partial unique index
    `cards_active_identity_uniq` is `(user_id, issuer, last_four) WHERE
    status = 'active'` (DESIGN.md §8.1, migration 20260516150000).
    Issuer is the canonical Tameru enum value (closed CHECK constraint),
    so exact equality is sufficient — no LOWER() needed.

    If for some reason the colliding row can't be re-read (race window
    where it was deleted between INSERT failure and SELECT), fall back
    to a 409 without an `existing_card_id` so the frontend at least
    surfaces a non-silent error.
    """
    try:
        lookup = (
            client.table("cards")
            .select("id, name, last_four")
            .eq("issuer", issuer)
            .eq("last_four", last_four)
            .eq("status", "active")
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
                    f"a card from issuer={issuer} ending {last_four} "
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
