"""Card statement-credit request/response models — Phase 1 (DESIGN.md §6.7, §8.17).

`CreditProposal` is the dual-role shape (same pattern as `CardProposal`):
`POST /card-credits/lookup` returns a list of them (server-minted
`client_request_id` per credit) and `POST /card-credits/confirm` accepts the
same list back after the user's edits. A manually-added credit is just a
`CreditProposal` the client builds with its own `client_request_id` — no
separate endpoint.

Monetary fields use `Decimal` for `numeric` round-trip safety (invariant 13).
`amount` is nullable: the lookup fails closed to null when the credit's amount
is quoted in a currency ≠ `home_currency` (same rule as the annual-fee prompt),
and the user types it. All amounts are in the user's single `home_currency`
(§8.7) — no per-credit currency, no FX.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Calendar-anchored cadence — mirrors the `card_credits.cadence` CHECK
# (migration 20260705120000). Anniversary anchoring is deferred (§6.7).
CreditCadence = Literal["monthly", "quarterly", "semiannual", "annual"]
# Lifecycle — mirrors the `card_credits.status` CHECK. "Stop tracking" →
# 'archived'; no 'deleted' value per the §8 status-column doctrine.
CreditStatus = Literal["active", "archived"]


class LookedUpCredit(BaseModel):
    """One credit as returned by the web_search lookup (pre-proposal).

    The lookup integration (`app/integrations/card_lookup.py`) emits these;
    the route decorates each with `card_id`, a minted `client_request_id`,
    `source_urls`, and `verified_at` to build a `CreditProposal`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    amount: Decimal | None = None
    cadence: CreditCadence
    merchant_hint: str | None = None


class CardCreditsLookupResult(BaseModel):
    """Result of the credit-list lookup (`lookup_card_credits`).

    `needs_manual=True` when the lookup produced no usable credits (empty,
    parse error, provider error) — the UI then offers only the manual-add
    path. Never a hard error: the lookup mirrors `lookup_card`'s never-raises
    contract, and the route returns HTTP 200 with an empty `credits` list.
    """

    model_config = ConfigDict(extra="forbid")

    credits: list[LookedUpCredit] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    needs_manual: bool = False
    raw_text: str | None = None


class CardCreditsLookupRequest(BaseModel):
    """HTTP body for `POST /card-credits/lookup`.

    Just the card id — the route resolves the card's name (for the web_search
    query) and validates ownership under RLS.

    Example: `{"card_id": "…uuid…"}`.
    """

    model_config = ConfigDict(extra="forbid")

    card_id: UUID


class CreditProposal(BaseModel):
    """Dual-role wire shape for one statement credit.

    1. **Lookup return** — `POST /card-credits/lookup` returns a list, one per
       discovered credit, each with a server-minted `client_request_id` and
       the lookup's `source_urls` / `verified_at`.
    2. **Confirm body item** — `POST /card-credits/confirm` accepts the same
       list back (with the user's edits / unchecks applied). A manual add is a
       `CreditProposal` the client builds with its own `client_request_id`,
       empty `source_urls`, and null `verified_at`.

    No row is written here — the confirm route hands these to the
    `card_credits_confirm` SECURITY INVOKER upsert RPC, which seeds the period
    bounds and dedups on the `(card_id, lower(name))` partial index.
    """

    model_config = ConfigDict(extra="forbid")

    card_id: UUID
    name: str
    amount: Decimal | None = None
    cadence: CreditCadence
    # Lowercased merchant token for the Phase-2 ledger bridge (e.g.
    # "lululemon"). Null disables the bridge for this credit.
    merchant_hint: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    # "last checked" — server-stamped by the lookup, round-tripped through the
    # client at confirm (same low-stakes trust posture as gemini_suggestion,
    # §8.2). Null for manually-added credits.
    verified_at: _dt.datetime | None = None
    client_request_id: UUID

    @field_validator("name")
    @classmethod
    def _v_name(cls, value: str) -> str:
        """Strip and reject empty credit names (the natural-key uses lower(name))."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        if len(stripped) > 120:
            raise ValueError("name is unreasonably long (>120 chars)")
        return stripped

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, value: Decimal | None) -> Decimal | None:
        """Reject negative allowances. Null is legal (fail-closed / not-yet-set)."""
        if value is not None and value < 0:
            raise ValueError(f"amount must be >= 0 (got {value})")
        return value

    @field_validator("merchant_hint")
    @classmethod
    def _v_merchant_hint(cls, value: str | None) -> str | None:
        """Lowercase + bound the merchant hint; empty → None."""
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if len(cleaned) > 80:
            raise ValueError("merchant_hint is unreasonably long (>80 chars)")
        return cleaned


class CardCreditsLookupResponse(BaseModel):
    """HTTP response for `POST /card-credits/lookup`.

    The proposals (with minted crids) plus the resolved card name so the UI
    can render the propose-confirm checklist without a second round-trip.
    `needs_manual=True` + empty `credits` means "found nothing — add manually."
    """

    card_id: UUID
    card_name: str
    credits: list[CreditProposal] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    needs_manual: bool = False


class CardCreditsConfirmRequest(BaseModel):
    """HTTP body for `POST /card-credits/confirm`.

    The (possibly edited) list of credits the user checked. All must reference
    the same owned, active card — the RPC drops any whose `card_id` is not the
    caller's active card. Bounded to a handful per card in practice.
    """

    model_config = ConfigDict(extra="forbid")

    credits: list[CreditProposal] = Field(min_length=1)


class CardCreditRow(BaseModel):
    """Response shape for a single `card_credits` row."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    card_id: UUID
    name: str
    amount: Decimal | None = None
    cadence: CreditCadence
    used_amount: Decimal
    current_period_start: _dt.date
    next_reset_date: _dt.date
    merchant_hint: str | None = None
    status: CreditStatus
    source_urls: list[str] = Field(default_factory=list)
    verified_at: _dt.datetime | None = None
    client_request_id: UUID
    created_at: _dt.datetime


class CardCreditListResponse(BaseModel):
    """`GET /card-credits?card_id=` and the `POST /card-credits/confirm` result.

    Both return the same shape: the confirm returns the rows that actually
    landed (idempotency-aware — a replay returns fewer), the GET returns the
    card's active credits.
    """

    items: list[CardCreditRow]


class CreditSuggestion(BaseModel):
    """Phase-2 ledger-bridge suggestion carried on `POST /transactions/confirm`.

    When a just-committed transaction's merchant + card match an active credit's
    `merchant_hint` (and the spend falls in that credit's current period), the
    confirm response carries this in its own `credit_suggestion` field —
    deliberately NOT the `insight` slot, so the entry-moment insight and the
    credit affordance never suppress each other (DESIGN.md §6.7). The UI renders
    "count {suggested_amount} toward {credit_name}?"; a tap POSTs
    `{transaction_id}` to `POST /card-credits/{credit_id}/apply`.

    `suggested_amount` is the display value — `min(transaction amount,
    remaining)` when the allowance is known — but the apply RPC clamps
    authoritatively, so a stale display can never over-count. `remaining` is
    null when the credit's `amount` (allowance) is unset.

    Example: `{"credit_id": "…", "credit_name": "Lululemon", "transaction_id":
    "…", "suggested_amount": "30.00", "remaining": "30.00"}`.
    """

    model_config = ConfigDict(extra="forbid")

    credit_id: UUID
    credit_name: str
    transaction_id: UUID
    suggested_amount: Decimal
    remaining: Decimal | None = None


class ApplyCreditUsageRequest(BaseModel):
    """HTTP body for `POST /card-credits/{credit_id}/apply` — the ledger tap.

    Carries only the matched transaction id. The server reads that
    transaction's amount + date under RLS (never a client-sent delta) and hands
    both ids to the `card_credit_apply_usage` atomic RPC, which increments
    `used_amount` clamped to `[0, allowance]` and guarded on same-card +
    `date >= current_period_start`. See DESIGN.md §6.7 Phase 2.

    Example: `{"transaction_id": "…uuid…"}`.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: UUID


class CardCreditPatchRequest(BaseModel):
    """Partial update body for `PATCH /card-credits/{id}`.

    Editable: `used_amount` (the set-used-amount action), `name`, `amount`,
    `cadence`, `status` (archive via `'archived'`). A `cadence` change
    recomputes `current_period_start` / `next_reset_date` from
    `credit_period_bounds()` server-side (the route handles it). `card_id`,
    `current_period_start`, `next_reset_date`, and `client_request_id` are not
    patchable.
    """

    model_config = ConfigDict(extra="forbid")

    used_amount: Decimal | None = None
    name: str | None = None
    amount: Decimal | None = None
    cadence: CreditCadence | None = None
    status: CreditStatus | None = None

    @field_validator("name")
    @classmethod
    def _vp_name(cls, v: str | None) -> str | None:
        """Strip non-null name patches; reject empty."""
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        if len(stripped) > 120:
            raise ValueError("name is unreasonably long (>120 chars)")
        return stripped

    @field_validator("used_amount", "amount")
    @classmethod
    def _vp_nonneg(cls, v: Decimal | None) -> Decimal | None:
        """Reject negative used_amount / amount patches. Null is legal."""
        if v is not None and v < 0:
            raise ValueError("value must be >= 0")
        return v


class CardCreditHistoryRow(BaseModel):
    """One closed-period snapshot from `card_credit_history` (Phase 2, §8.18).

    Written by the `reset_card_credits()` sweep at each period rollover;
    read-only to the user. Powers the Credits page "last {period} you used $X".
    `amount` mirrors the closed period's allowance (nullable, as on the live
    row); `used_amount` is what was actually used before the reset zeroed it.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    card_credit_id: UUID
    name: str
    amount: Decimal | None = None
    used_amount: Decimal
    period_start: _dt.date
    period_end: _dt.date
    created_at: _dt.datetime


class CardCreditHistoryResponse(BaseModel):
    """`GET /card-credits/{id}/history` response — newest closed period first."""

    items: list[CardCreditHistoryRow]
