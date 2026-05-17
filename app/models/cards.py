"""Card request/response models — Day 14.

`CardProposal` is produced by Day 14's `propose_card` tool and by
`POST /cards/lookup`; both feed it to `POST /cards/confirm` for commit.
Defining it here keeps the tool return shape and the wire body identical —
the same dual-role pattern as `TransactionProposal` (DESIGN.md §6.2 + §7.2).

All monetary amounts use `Decimal` for `numeric` column round-trip safety
(DESIGN.md §8.2, invariant 13). `multipliers` stays as `dict[str, float]`
because JSON numbers in `multipliers jsonb` are not money and the precision
floor (0.5×, 1×, 2×, …) is well above float-arithmetic concerns; treating
multipliers as `Decimal` would force re-coercion at every JSON boundary
without buying anything.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


CardNetwork = Literal["visa", "mastercard", "amex", "discover", "other"]
CardProgram = Literal["UR", "MR", "TYP", "Bilt", "Other"]
# Closed-enum issuer (DESIGN.md §8.1, migration
# 20260516140000_cards_uniqueness_by_issuer.sql). Eliminates the
# case-sensitivity and variant collisions ("Chase" vs "chase" vs
# "Chase Bank") that would defeat the (user_id, issuer, last_four)
# partial unique index. `other` is the escape hatch for issuers we
# haven't enumerated yet; the user can patch later.
CardIssuer = Literal[
    "chase",
    "amex",
    "citi",
    "capital_one",
    "discover",
    "bank_of_america",
    "wells_fargo",
    "usaa",
    "bilt",
    "barclays",
    "us_bank",
    "synchrony",
    "other",
]


# Per DESIGN.md §6.1 — domain allowlist for the Claude web_search call.
# Mutating this list changes the upstream sources Claude can cite for card
# lookups. The CLAUDE.md model usage table mirrors this set; keep them in
# sync. Issuer domains are inferred at call time and appended per request.
CARD_LOOKUP_ALLOWED_DOMAINS: tuple[str, ...] = (
    "nerdwallet.com",
    "thepointsguy.com",
    "uscreditcardguide.com",
    "doctorofcredit.com",
)


class CardLookupResult(BaseModel):
    """Result of the Claude web_search-backed card lookup (Day 14).

    Returned by `app/integrations/card_lookup.py::lookup_card`. The
    proposal-building path (POST /cards/lookup, propose_card tool impl)
    converts a successful result into a `CardProposal`; the user reviews
    and tweaks before `POST /cards/confirm` commits the row.

    Example response (happy path):
        {
            "program": "UR",
            "multipliers": {"Dining": 3, "Travel": 3, "Other": 1},
            "annual_fee": 95,
            "issuer": "Chase",
            "source_urls": [
                "https://www.nerdwallet.com/.../sapphire-preferred",
                "https://thepointsguy.com/.../csp-review",
            ],
            "needs_manual": false
        }

    Example response (low-confidence / failure path):
        {
            "program": null,
            "multipliers": {},
            "annual_fee": null,
            "issuer": null,
            "source_urls": [],
            "needs_manual": true,
            "raw_text": "Sorry, couldn't find current info..."
        }
    """

    model_config = ConfigDict(extra="forbid")

    program: CardProgram | None = None
    # Network is product-fixed for nearly every card (Chase Sapphire = Visa,
    # all Amex = Amex, Citi Double Cash = Mastercard, etc.). The web_search
    # lookup extracts it so the chat agent doesn't have to ask the user the
    # one piece of card metadata most users don't know off the top of their
    # head. None when sources don't agree or the card is genuinely available
    # on multiple networks (rare — Costco Visa vs older versions, etc.).
    network: CardNetwork | None = None
    # Issuer is the actual uniqueness tiebreaker (DESIGN.md §8.1). The
    # lookup maps web-search strings ("Chase", "American Express", "Citibank")
    # onto the closed enum via _normalize_issuer() in card_lookup.py.
    # None when the lookup couldn't determine the issuer — the caller
    # falls back to "other" on the CardProposal and flags needs_manual.
    issuer: CardIssuer | None = None
    multipliers: dict[str, float] = Field(default_factory=dict)
    annual_fee: Decimal | None = None
    source_urls: list[str] = Field(default_factory=list)
    needs_manual: bool = False
    # Only set when needs_manual=True — gives the UI a debugging breadcrumb
    # if the user wants to know why the lookup fell back. Never displayed
    # raw to non-debug users; the manual-fill form is the user-facing path.
    raw_text: str | None = None


class CardProposal(BaseModel):
    """Wire shape for a chat- or onboarding-originated card create.

    Same dual-role pattern as `TransactionProposal`:

      1. **Agent tool return.** `propose_card({network, last_four, program,
         alias?})` fills in multipliers / annual_fee / source_urls via the
         web_search lookup and returns this shape. The client renders it
         as a parse card; the row is NOT written here.
      2. **Endpoint body.** `POST /cards/confirm` accepts the same shape.
         The client posts the proposal back verbatim after the user taps
         "looks right" (with any inline edits applied).

    `network` + `last_four` are required because they together form the
    active-identity uniqueness key (see migration
    `20260516130000_cards_network_and_deactivated_at.sql`). Without them
    the 409 collision flow cannot work; the propose-then-confirm contract
    breaks down. The chat system prompt teaches Claude to ask the user
    for either field if missing rather than guess.
    """

    model_config = ConfigDict(extra="forbid")

    network: CardNetwork
    # Nullable on the tool-return shape: `propose_card` may return a
    # proposal with `last_four=None` when the user didn't say it in chat.
    # The parse-card UI collects it before the user can tap "looks right";
    # `POST /cards/confirm` rejects payloads that still have it missing
    # at commit time (the column is NOT NULL on the DB).
    last_four: str | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        pattern=r"^\d{4}$",
    )
    name: str
    issuer: CardIssuer
    program: CardProgram = "Other"
    multipliers: dict[str, float] = Field(default_factory=dict)
    annual_fee: Decimal | None = None
    source_urls: list[str] = Field(default_factory=list)
    color: str | None = None
    alias: str | None = None
    needs_manual: bool = False

    @field_validator("name")
    @classmethod
    def _v_name(cls, value: str) -> str:
        """Strip and reject empty-string card names."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        return stripped

    @field_validator("annual_fee")
    @classmethod
    def _v_annual_fee(cls, value: Decimal | None) -> Decimal | None:
        """Reject negative annual fees. Zero is legal (no-fee cards)."""
        if value is not None and value < 0:
            raise ValueError(f"annual_fee must be >= 0 (got {value})")
        return value


class CardLookupRequest(BaseModel):
    """HTTP body for `POST /cards/lookup`.

    Just the card's display name. Network and last_four are entered on the
    onboarding form / by Claude in the tool call; they don't change the
    lookup query. The lookup itself searches authoritative sources for
    the rewards program data attached to the name.

    Example: `{"name": "Chase Sapphire Reserve"}`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _v_name(cls, value: str) -> str:
        """Strip and reject empty-string lookups (would search for nothing)."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        if len(stripped) > 120:
            raise ValueError("name is unreasonably long (>120 chars)")
        return stripped


class CardLookupResponse(BaseModel):
    """HTTP response for `POST /cards/lookup`.

    The lookup result merged with the user-supplied name so the UI can
    render a parse card without a second round-trip.
    """

    name: str
    lookup: CardLookupResult


class CardConfirmRequest(CardProposal):
    """HTTP body for `POST /cards/confirm`.

    Identical in shape to `CardProposal` today — the client posts the
    proposal back verbatim after the user taps "looks right." Stub
    subclass for the same reason `TransactionConfirmRequest` exists:
    OpenAPI documents this endpoint's input by its HTTP role, and the
    tool return type and the endpoint body evolve independently.
    """

    pass


CardStatus = Literal["active", "deleted"]


class CardRow(BaseModel):
    """Response shape for a single `cards` row.

    Mirrors the `cards` table after the §8 status-column migration: `status`
    encodes the lifecycle (replaces the prior `active` boolean), and
    `deleted_at` lands when the row is soft-deleted (renamed from
    `deactivated_at`).
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    name: str
    issuer: CardIssuer
    network: CardNetwork
    program: CardProgram
    multipliers: dict[str, float] = Field(default_factory=dict)
    annual_fee: Decimal | None = None
    last_four: str | None = None
    color: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    status: CardStatus
    deleted_at: _dt.datetime | None = None
    created_at: _dt.datetime


class CardListResponse(BaseModel):
    """`GET /cards` response.

    `include_inactive=true` callers see both active and deleted cards in
    one list; the frontend filter (DESIGN.md §8.1 frontend filter rules)
    distinguishes them by `status` + `deleted_at`. No pagination — cards
    are bounded to ~10 per user lifetime.
    """

    items: list[CardRow]


class CardPatchRequest(BaseModel):
    """Partial update body for `PATCH /cards/{id}`.

    Editable fields: `name`, `alias` (via name), `multipliers`, `annual_fee`,
    `color`, `program`. `network`, `last_four`, `issuer`, `status`, and
    `deleted_at` are NOT patchable — those represent card identity, which
    the soft-delete-then-re-add flow handles (DESIGN.md §8.1).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    program: CardProgram | None = None
    multipliers: dict[str, float] | None = None
    annual_fee: Decimal | None = None
    color: str | None = None

    @field_validator("name")
    @classmethod
    def _vp_name(cls, v: str | None) -> str | None:
        """Strip non-null name patches and reject empty names."""
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        return stripped

    @field_validator("annual_fee")
    @classmethod
    def _vp_annual_fee(cls, v: Decimal | None) -> Decimal | None:
        """Reject non-null negative annual_fee patches."""
        if v is not None and v < 0:
            raise ValueError(f"annual_fee must be >= 0 (got {v})")
        return v


# Domain-error payload shapes — the route handler raises these as 409 detail.
# Centralized so the frontend's API client and the route emit a single shape.


class ActiveCardExistsDetail(BaseModel):
    """409 body when `POST /cards/confirm` collides with an active row.

    The frontend uses `existing_card_id` + `existing_card_name` to render
    the "you already have *{name}* ending {last_four} — edit it instead?"
    affordance, linking to the PATCH sheet.
    """

    code: Literal["active_card_exists"] = "active_card_exists"
    message: str
    existing_card_id: UUID
    existing_card_name: str
    existing_card_last_four: str | None
