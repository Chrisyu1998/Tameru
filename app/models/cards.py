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


# Network is a closed enum. `jcb` and `diners` were added for Tier 3
# (international cards — DESIGN.md §6.6): JCB is dominant in Japan and
# common in Taiwan. Mirrors the `cards_network_check` CHECK (migration
# 20260602120000) — both layers must agree.
CardNetwork = Literal[
    "visa", "mastercard", "amex", "discover", "jcb", "diners", "other"
]
CardProgram = Literal["UR", "MR", "TYP", "Bilt", "Other"]
# Per-card region — drives reward-lookup routing (US sources + category
# multipliers vs. JP/TW base-rate path). A property of the *card*, not the
# user, so a wallet can hold a US card and a TW card at once and each
# routes independently. DESIGN.md §6.6 Tier 3.
CardRegion = Literal["US", "JP", "TW"]
# Closed-enum issuer (DESIGN.md §8.1, §6.6, migrations
# 20260516140000_cards_uniqueness_by_issuer.sql and
# 20260602120000_cards_intl_enums_and_columns.sql). Eliminates the
# case-sensitivity and variant collisions ("Chase" vs "chase" vs
# "Chase Bank") that would defeat the (user_id, issuer, last_four)
# partial unique index. Tier 3 widened it with the top ~6 JP and ~6 TW
# issuers; the `card_issuers` reference table carries each key's
# region/domain/display metadata. Keys are hyphen-free so the chat
# `card_ref` handle ({issuer}-{last_four}) splits unambiguously. `other`
# is the escape hatch for issuers we haven't enumerated yet.
CardIssuer = Literal[
    # US
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
    # JP
    "rakuten",
    "smbc",
    "jcb",
    "aeon",
    "epos",
    "saison",
    # TW
    "cathay",
    "esun",
    "ctbc",
    "taishin",
    "fubon",
    "union",
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
    # Tier 3 (DESIGN.md §6.6) — the JP/TW reward shape. Outside the US the
    # lookup captures a base earn rate (percent, e.g. 1.0 for 1%) and a
    # free-text rewards label ("Rakuten Points", "現金回饋") instead of
    # `multipliers`. None on US cards (which use `multipliers`). Category
    # multipliers and promos are deliberately NOT captured for JP/TW — they
    # are partner-economy / user-selected / mobile-pay driven and a
    # one-shot, no-refresh lookup can't represent them stably.
    base_reward_rate: Decimal | None = None
    rewards_currency: str | None = None
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
    # Tier 3 (DESIGN.md §6.6) — JP/TW base-rate reward shape, carried from
    # the lookup through to `/cards/confirm`. Mutually-exclusive-in-practice
    # with `multipliers`: US cards fill multipliers, JP/TW cards fill these.
    # `region` is NOT on the proposal wire — the confirm route recomputes it
    # server-side from the issuer (falling back to home_currency) so a forged
    # client can't mislabel a card's region.
    base_reward_rate: Decimal | None = None
    rewards_currency: str | None = None
    # Tier 3 (DESIGN.md §6.6) — the region the user picked / the lookup used.
    # `/cards/confirm` only honors it when the issuer is unenumerated
    # (`other`) — a known issuer's region is server-pinned and a forged value
    # is ignored. None means "no explicit pick"; confirm then falls back to
    # the home-currency guess. This is what makes the add-card region selector
    # stick for an `other`-issuer card (e.g. a TWD user adding a small US bank).
    region: CardRegion | None = None
    annual_fee: Decimal | None = None
    # Day 19b — optional renewal date for card annual-fee tracking. When
    # set alongside `annual_fee > 0`, `POST /cards/confirm` creates a
    # companion `subscriptions` row (frequency='annual', category=
    # 'Memberships', name='{card_name} annual fee') so the pg_cron
    # auto-logger logs the AF on each anniversary. Optional — users who
    # don't know the date skip it; the card still saves and AF tracking
    # is just unavailable. Cannot be inferred from web_search (per-user
    # fact). DESIGN.md §6.5, §8.1.
    next_annual_fee_date: _dt.date | None = None
    source_urls: list[str] = Field(default_factory=list)
    color: str | None = None
    alias: str | None = None
    needs_manual: bool = False
    # Stable per-proposal identifier. Server-mints at `propose_card` time;
    # the client posts it back unchanged at `/cards/confirm` so the row's
    # `client_request_id` column matches the persisted `tameru_proposal`
    # block's `result.client_request_id`. The chat-rehydrate annotation
    # (`_annotate_committed_proposals`) joins on this — disambiguates
    # two same-name cards (e.g. "Amex Gold" 1234 vs "Amex Gold" 5678),
    # which a name-only join can't. See migration
    # `20260517120000_cards_client_request_id.sql` and DESIGN.md §8.1.
    #
    # Not an idempotency token in the transactions sense — the partial
    # unique index on `(user_id, issuer, last_four) WHERE status =
    # 'active'` still owns DB-level dedup. This is a *join key*. The
    # `/cards/confirm` route DOES short-circuit on same-crid replay
    # (returns the existing row) so a network retry of the exact same
    # proposal is harmless.
    client_request_id: UUID

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

    @field_validator("next_annual_fee_date")
    @classmethod
    def _v_next_annual_fee_date(cls, value: _dt.date | None) -> _dt.date | None:
        """Reject past renewal dates, with one day of timezone slack.

        Past-date rejection prevents the pg_cron auto-logger from
        immediately firing on a date the user typed by mistake. Same-day
        renewals are legitimate (the card might charge the AF today).
        DESIGN.md §6.5 forward-only rule.

        The 1-day slack mirrors `_DATE_FUTURE_SLACK` on the transactions
        confirm route, in the opposite direction: Pydantic validators
        have no user context, so "today" here is server-UTC — for a
        US-evening user (UTC is already tomorrow) their local *today* is
        UTC *yesterday* and a strict `< today` check would reject it.
        The slack also keeps an offline-queued confirm carrying
        `next_annual_fee_date == today` from 422ing when the queue
        drains after UTC midnight (audit P3-30/P3-31).
        """
        if value is not None and value < _dt.date.today() - _dt.timedelta(days=1):
            raise ValueError(
                f"next_annual_fee_date must be today or later (got {value})"
            )
        return value


class CardLookupRequest(BaseModel):
    """HTTP body for `POST /cards/lookup`.

    Just the card's display name. Network and last_four are entered on the
    onboarding form / by Claude in the tool call; they don't change the
    lookup query. The lookup itself searches authoritative sources for
    the rewards program data attached to the name.

    Example: `{"name": "Chase Sapphire Reserve"}`.
    Example (Tier 3): `{"name": "Rakuten Card", "region": "JP"}`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    # Tier 3 (DESIGN.md §6.6) — which region's sources / reward model the
    # lookup uses. Optional: when omitted, the route derives it from the
    # user's `home_currency` (JPY→JP, TWD→TW, else US). The add-card form
    # defaults the selector from home_currency but lets the user override
    # (the "US expat in Taiwan adding an old US card" case).
    region: CardRegion | None = None

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
    # Tier 3 (DESIGN.md §6.6). `region` is NOT NULL on the table (default
    # 'US'); base-rate fields are null on US cards.
    region: CardRegion = "US"
    base_reward_rate: Decimal | None = None
    rewards_currency: str | None = None
    multipliers: dict[str, float] = Field(default_factory=dict)
    annual_fee: Decimal | None = None
    last_four: str | None = None
    color: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    status: CardStatus
    deleted_at: _dt.datetime | None = None
    created_at: _dt.datetime
    # Stable per-proposal join key — see `CardProposal.client_request_id`.
    client_request_id: UUID


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
    `color`, `program`, `next_annual_fee_date`. `network`, `last_four`,
    `issuer`, `status`, and `deleted_at` are NOT patchable — those represent
    card identity, which the soft-delete-then-re-add flow handles
    (DESIGN.md §8.1).

    `next_annual_fee_date` is a *virtual* field — it doesn't write to a
    column on `cards` (the renewal date lives on the companion AF
    subscription's `next_billing_date`). When present in the patch, the
    route routes through the `update_card_af` SECURITY DEFINER RPC,
    which atomically updates `cards.annual_fee` (if also patched) and
    the companion AF subscription's `amount` / `next_billing_date`.
    Setting `next_annual_fee_date = null` cancels the companion AF
    subscription (stop tracking); the cards row keeps its
    `annual_fee` snapshot. DESIGN.md §6.5.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    program: CardProgram | None = None
    multipliers: dict[str, float] | None = None
    annual_fee: Decimal | None = None
    color: str | None = None
    next_annual_fee_date: _dt.date | None = None

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

    @field_validator("next_annual_fee_date")
    @classmethod
    def _vp_next_annual_fee_date(
        cls, v: _dt.date | None
    ) -> _dt.date | None:
        """Reject past renewal-date patches. Null is legal (stop tracking).

        Same rule (including the 1-day timezone slack) as
        `CardProposal._v_next_annual_fee_date` — past dates would make
        pg_cron auto-log immediately on the next run, but a strict
        server-UTC `< today` check rejects a US-evening user's local
        today (audit P3-30). Explicit `null` means "stop tracking the
        AF" and bypasses the date check.
        """
        if v is not None and v < _dt.date.today() - _dt.timedelta(days=1):
            raise ValueError(
                f"next_annual_fee_date must be today or later (got {v})"
            )
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
