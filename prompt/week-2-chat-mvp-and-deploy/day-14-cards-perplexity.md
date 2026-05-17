# Day 14 — Card management + Claude `web_search` multiplier lookup (chat-integrated + onboarding)

> **File name retained for git history** but the integration is **Claude `web_search`**, not Perplexity. The vendor switch is captured in DESIGN.md §0 and §6.1. Perplexity is no longer a dependency.

## Goal

Users add cards two ways:

1. **Onboarding (UX frame 4 — "Add First Card")** — a dedicated setup screen during first-run. Text input + network selector + last-4 input + suggestion chips, then lookup/confirm inline on that screen.
2. **Post-onboarding — via chat.** User says "add my Chase Sapphire Reserve, Visa ending 1234"; Claude calls the `propose_card` tool (registered in this day's `TOOL_REGISTRY` update); the tool impl invokes Claude with the `web_search` server tool restricted via `allowed_domains` and returns a `CardProposal`; React renders the proposal as a parse card in the chat; user taps "looks right" → `POST /cards/confirm` writes the row.

**Tool registration moved here from Day 9.** Day 9's earlier plan would have registered `propose_card` as a stub returning `"card_lookup_unavailable"` until this day landed. That would have meant 5–6 days of Claude seeing `propose_card` in its tool list and calling it with no working backend, surfacing tool-result errors to users. Registering the tool **only when the web_search-backed lookup and `POST /cards/confirm` both exist** is the cleaner contract: tools that can't end-to-end commit are not in `TOOL_REGISTRY`.

Both paths use the same lookup and the same `POST /cards/confirm` endpoint. There is no standalone `AddCard.tsx` page after onboarding — "add via tameru ai" is the post-onboarding affordance (UX frames 18 and 20).

## Read first

- `DESIGN.md` §6.1 (Claude `web_search` card lookup), §8.1 (`cards` schema — `network`, `last_four`, `source_urls`, `deactivated_at`, partial unique index), §7.2 (`propose_card` tool shape).
- `CLAUDE.md` invariant 8 (the propose-then-confirm pattern this day implements for cards).
- Day 9b's `propose_transaction` for the propose-tool template — `propose_card` follows the same shape: tool returns a proposal, does not `.insert()`.
- `UX_PROMPT.md` frames 4 (Add First Card — onboarding), 18 (Cards List), 19 (delete swipe), 20 (Cards Empty).
- [Claude web search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool) for the `web_search_20250305` definition, `allowed_domains`, citation shape, and `$10 per 1,000 searches` pricing.

## Deliverables

### Migration — `cards` schema updates

Add to the next `supabase/migrations/` file:

```sql
ALTER TABLE cards
  ADD COLUMN network text
    CHECK (network IN ('visa', 'mastercard', 'amex', 'discover', 'other')),
  ADD COLUMN deactivated_at timestamptz;

-- Backfill: existing rows get 'other' (no real cards exist in v1 prod yet,
-- but the column is NOT NULL after backfill).
UPDATE cards SET network = 'other' WHERE network IS NULL;
ALTER TABLE cards ALTER COLUMN network SET NOT NULL;

-- Active-identity uniqueness. Inactive rows are exempt by design (DESIGN.md §8.1).
-- Note: the active-identity index is keyed on `issuer`, not `network`
-- (corrected post-Day-14 in migration
-- 20260516140000_cards_uniqueness_by_issuer.sql). See DESIGN.md §8.1
-- "Constraints" for the rationale.
CREATE UNIQUE INDEX cards_active_identity_uniq
  ON cards (user_id, issuer, last_four)
  WHERE active = true;
```

`last_four` is already in the schema (text, UI identification per DESIGN.md §8.1). Treat it as required on the proposal path going forward; the existing column nullability stays so historical rows aren't disturbed.

### `app/integrations/card_lookup.py` (new — replaces the never-built `perplexity.py`)

- `lookup_card(card_name: str, *, jwt: str) -> CardLookupResult` — calls Claude Haiku 4.5 with the `web_search_20250305` server tool enabled. The Claude call is configured as:
  - `allowed_domains = ["nerdwallet.com", "thepointsguy.com", "uscreditcardguide.com", "doctorofcredit.com"]` plus the inferred issuer domain (`chase.com`, `americanexpress.com`, `citi.com`, etc.) — issuer inference is a simple keyword map; if unknown, omit and rely on the allowlist.
  - `max_uses = 3` to bound cost (one lookup ≈ 1–2 searches).
  - System prompt: "Extract `program` (rewards program name), `multipliers` (object mapping category → number), `annual_fee` (USD numeric), and `issuer` for the credit card. Return strict JSON. If sources disagree or data isn't found, set the missing field to null."
  - User prompt: the card name.
- Parses the JSON response. Pulls citations from each `web_search_result_location` block (`url`, `title`). Stores URLs into `CardLookupResult.source_urls` (list[str]) and returns them on the proposal payload.
- If parse fails, key fields are null, or `web_search_tool_result_error` fires (`max_uses_exceeded`, `too_many_requests`, `unavailable`), return `{needs_manual: true, raw_text}` — the parse card surfaces the manual-fill path.
- Logs to `ai_call_log` with `provider="anthropic"`, `model="claude-haiku-4-5"`, `task_type="card_lookup"` via the user-JWT `log_ai_call` helper (Day 4, invariant 14). No service role. `web_search_requests` from `usage.server_tool_use` goes into the cost calculation if you're computing $$ per call.

### `app/routes/cards.py`

- `POST /cards/lookup` — body: `{name}`. Calls `lookup_card`, returns the proposed card data + citations. Used by the onboarding flow *and* by the `propose_card` tool internals.
- **`POST /cards/confirm`** — body: `CardProposal` payload (`network` **required**, `issuer` **required** (closed-enum CardIssuer), `last_four` **required at commit**, `program`, `multipliers`, `annual_fee`, `source_urls`, `alias?`). Inserts the row. Returns the created card. Note: `last_four` is nullable on the wire shape (`propose_card` may return a proposal without it mid-conversation), but the route 422s if the commit payload is still missing it — the partial unique index would otherwise treat null-last_four rows as distinct and silently allow duplicates.
  - **Collision handling** (matches DESIGN.md §8.1):
    - If the `cards_active_identity_uniq` partial index raises a unique-violation → return **HTTP 409** with `{code: "active_card_exists", existing_card: {id, name, last_four, deactivated_at: null}}`. The frontend renders a "you already have Amex Plat ending 1234 — edit it instead?" affordance linking to the PATCH sheet. The collision is keyed on `(issuer, last_four)` — see DESIGN.md §8.1 for why network is the wrong tiebreaker.
    - If only an inactive row matches, the partial index does NOT fire — the insert succeeds and a new row is created. Per DESIGN.md §8.1, soft-deleted rows are never revived: a fresh row gets fresh multipliers, fresh annual_fee, fresh `card_id`, and historical transactions stay attached to the previous soft-deleted row.
  - **No `client_request_id` idempotency here.** Cards are low-frequency (3–5 per user lifetime); the worst case on offline-queue replay is a duplicate the user deletes. Don't add the column or the partial unique index "for consistency" with transactions.
- **No `POST /cards` (direct write from a free-form user form).** The only commit path is `/confirm` after a proposal the user saw.
- `GET /cards` — list **active** cards (`active=true`). Inactive rows are reachable only through the spending-breakdown filter path described below, never through this list endpoint.
- `PATCH /cards/{id}` — edit (used from More → My Cards list, UX frame 18, tap-to-edit). Edits of inactive rows are allowed but uncommon — surfaces only from the breakdown filter, not from the active-cards list.
- `DELETE /cards/{id}` — soft delete: `UPDATE cards SET active = false, deactivated_at = now() WHERE id = $1`. Swipe-left on a row (UX frame 19).

### `propose_card` — `app/agent/tools.py` (new, deferred from Day 9)

```text
propose_card({program, network?, last_four?, alias?}) → CardProposal
```

- **Only `program` (the card name) is required.** `network` and `last_four` are optional tool args (Day 14 follow-up — issuer and network are derived by the lookup; last_four is collected by the parse-card UI before commit). The system prompt teaches Claude to pass `network` only when the user explicitly named it ("my Visa Sapphire") and `last_four` only when the user said it ("ending 4321"); never to ask the user which network or issuer their card is on. `issuer` is filled by the lookup from the card name and can be edited on the parse card.
- Tool implementation:
  1. Call `lookup_card(name=program)` to fill multipliers, annual_fee, issuer, and source_urls. If `lookup_card` returns `{needs_manual: true, ...}`, return the proposal with empty multipliers and `needs_manual=true` so the parse card surfaces the manual-fill path.
  2. Build and return a `CardProposal` (define in `app/models/cards.py` if not already present — mirrors the Day 5 `TransactionProposal` pattern).
  3. **Does not `.insert()` into `cards`.** The invariant-guard test from Day 9b covers this — `propose_card` must not be in `ALLOWED_DIRECT_WRITE_TOOLS`.

No `client_request_id` on card proposals — cards are low-frequency (3–5 per user lifetime), and a duplicate from an offline-queue replay is recoverable by a single delete.

Add `propose_card` to `TOOL_REGISTRY` in this day. Update the system prompt's tool descriptions to include `propose_card` and bump `PROMPT_VERSION`. Bumping the version busts the prompt cache once; the next turn re-warms it.

### Pydantic models — `app/models/cards.py`

```python
class CardLookupResult(BaseModel):
    program: str | None
    multipliers: dict[str, float]
    annual_fee: float | None
    issuer: str | None
    source_urls: list[str]
    needs_manual: bool = False
    raw_text: str | None = None  # only populated when needs_manual=True

class CardProposal(BaseModel):
    network: Literal["visa", "mastercard", "amex", "discover", "other"]
    last_four: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")
    program: str
    multipliers: dict[str, float]
    annual_fee: float | None
    source_urls: list[str]
    alias: str | None = None
    needs_manual: bool = False
```

### Frontend — onboarding (UX frame 4)

`frontend/src/pages/AddFirstCard.tsx`:

- 2-step progress indicator (step 1 of 2, accent).
- Card name input (sunken, pill-shape) + 3 suggestion chips.
- **Network selector** (Visa / Mastercard / Amex / Discover / Other — segmented control or chip row).
- **Last-4 input** (numeric, 4 digits, validated client-side).
- On submit: spinner + "looking up multipliers…" then render the proposed card preview (same visual layout as the chat parse card) with multipliers as editable rows.
- "add card" primary (enabled only once name + network + 4-digit last_four are present *and* the lookup has returned) → `POST /cards/confirm`.
- **409 handling:** if the confirm returns `active_card_exists`, render an inline banner: "you already have *{existing.name}* ending {existing.last_four}. edit it instead?" + a tap-to-edit chip. Do not let the user retry the same `(network, last_four)` without a change.
- Manual fallback path when `needs_manual: true`: editable blank form, same network + last_four already filled in.
- "skip for now" tertiary link → proceeds to the next onboarding step.

### Frontend — post-onboarding

`frontend/src/pages/Cards.tsx` (UX frame 18):

- Top bar: back chevron + "my cards".
- List of card tiles. Each tile: colored left-edge stripe · card name · network + last-4 chip · program chip · multiplier chips.
- Swipe-left on a tile reveals the terracotta delete panel (UX frame 19). Confirming dispatches `DELETE /cards/{id}` (sets `active=false`, `deactivated_at=now()`).
- Tap tile → PATCH edit sheet.
- Empty state (UX frame 20): card icon + "no cards yet" + "add via tameru ai" primary (deep-links to the chat half-sheet pre-seeded with a `chip_payload: "add-first-card"` suggestion chip — the chip's tap message is *"let's add my first card"*).
- AI hint footer on the populated list: "✨ add a new card via tameru ai →" (taps into chat).

**There is no standalone `AddCard.tsx` page after onboarding.** The post-onboarding add path is chat-only. This day's `propose_card` tool is the entry point; Day 10's `ParseCard` component renders the preview.

### Frontend — spending-breakdown filter semantics (NEW)

The breakdown surfaces (per-category drilldown, chat-rendered card breakdowns, weekly delta) all follow three rules. See DESIGN.md §6.1 and §8.1 for the design rationale.

**Rule 1 — totals always include inactive cards.** Sum-by-category, sum-by-month, weekly delta, year-to-date math sum across `active = true` AND `active = false`. Transaction reads do **not** filter by `cards.active`. Anything else and "total spend" silently stops matching "sum of per-card spend" the moment a card is deleted.

**Rule 2 — filter dropdown is dynamic.** The "filter by card" picker shows:
- **All active cards** (even with zero transactions in the current view).
- **Inactive cards with ≥1 transaction in the current view's date range.**
- Inactive cards with no transactions in scope are hidden.

The frontend computes this set client-side from the transaction list + the card list (which now must include inactive cards for this view — add `?include_inactive=true` to `GET /cards` or expose a second endpoint `GET /cards/all` returning both buckets). Pick whichever is cleaner; an `include_inactive` query param is the lower-surface choice.

**Rule 3 — collision labels.** When the same `(network, last_four)` exists as both an active and an inactive row (legal — soft-delete + re-add, see §8.1), labels resolve as:

| Row state | Label |
|---|---|
| Active | `{card.name} · {last_four}` |
| Inactive, no collision | `{card.name} · {last_four} · closed {MMM YYYY}` — muted color |
| Inactive, collides with active | Same as above. The "closed {MMM YYYY}" suffix is the disambiguator. |

`closed MMM YYYY` is derived from `card.deactivated_at`.

### Tests

- `tests/test_cards.py`:
  - Mocked Claude `web_search` responses; round-trip `lookup → confirm → saved row` with citations correctly stored in `source_urls`.
  - `POST /cards/confirm` validates the payload and rejects missing `network` / malformed `last_four`.
  - **Collision test 1 (active blocks active):** insert active card `(visa, 1234)`; second confirm of `(visa, 1234)` returns 409 `active_card_exists`.
  - **Collision test 2 (soft-delete allows re-add):** insert active `(visa, 1234)`, soft-delete it, confirm a new `(visa, 1234)` — second row is created with a new `card_id`; old row is unchanged; transactions linked to the old row are still present.
  - **Network disambiguation:** `(visa, 1234)` active and `(amex, 1234)` active coexist with no 409.
  - **`deactivated_at` is set** by `DELETE /cards/{id}`.
- RLS: user A cannot GET / PATCH / DELETE user B's cards.
- AI log check: one `ai_call_log` row per `lookup_card` call with `provider="anthropic"`, `model="claude-haiku-4-5"`, `task_type="card_lookup"`.
- Web search organization-enable check: failing call surfaces a clear error message; covered by a mocked-error test.

## Don't

- Don't pre-seed a card library. Web search is the source of truth at lookup time.
- Don't expose Claude's raw `web_search_tool_result` blocks to the user — parse to structured JSON and surface only `program`, `multipliers`, `annual_fee`, and the citation URLs.
- Don't store citation URLs as a single string; use the `text[]` column.
- Don't build a standalone `AddCard.tsx` page for post-onboarding use. Chat is the add surface after onboarding (invariant 8).
- Don't write to `cards` from inside `propose_card`. The tool returns a proposal; `POST /cards/confirm` commits. The invariant-guard test from Day 9b will fail if `propose_card` calls `.insert()`.
- Don't register `propose_card` in `TOOL_REGISTRY` before this day's `lookup_card` + `POST /cards/confirm` exist. Partial tool registration produces a worse UX than no tool.
- Don't add `last_four` as part of a global unique index. The constraint is **partial on `WHERE active = true`** — DESIGN.md §8.1 — so soft-deleted rows do not block re-adds.
- Don't revive soft-deleted card rows on re-add. New row, fresh `card_id`. See §8.1 rationale.
- Don't filter transactions by `cards.active` in breakdown queries. Rule 1 above. Totals must reconcile.

## Done when

- Onboarding: name + network (Visa) + last-4 (1234) → "Chase Sapphire Reserve" produces sane multipliers + ≥2 citations from allowlisted domains; user confirms; row exists in `cards` with `network='visa'`, `last_four='1234'`.
- Post-onboarding: saying "add my Amex Gold ending 4321" in chat fires `propose_card`, renders a parse card in chat, user confirms, row is added, Cards list reflects it.
- If user says only "add my Amex Gold" (no last 4), Claude re-prompts for the last 4 before retrying the tool.
- Manual fallback path works on onboarding when web_search returns low-confidence or fails.
- `ai_call_log` shows the lookup calls with `provider="anthropic"`, `task_type="card_lookup"`, written via the user-JWT path.
- Adding a second Amex Plat ending 1234 (when one is already active) returns 409 and surfaces the existing-card affordance.
- Deleting an Amex Plat 1234 then re-adding Amex Plat 1234 produces two rows: one inactive (with `deactivated_at` set), one active; transactions linked to the inactive row are unaffected.
- Spending breakdown filter shows both rows for the same `(amex, 1234)` collision, with the inactive one labeled `closed {MMM YYYY}` and muted.
- Swipe-left on a card tile reveals the delete panel; confirming sets `active=false` and `deactivated_at`.
