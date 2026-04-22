# Day 11 — Card management + Perplexity Sonar multiplier lookup (chat-integrated + onboarding)

## Goal

Users add cards two ways:

1. **Onboarding (UX frame 4 — "Add First Card")** — a dedicated setup screen during first-run. Text input + suggestion chips, then lookup/confirm inline on that screen.
2. **Post-onboarding — via chat.** User says "add my Chase Sapphire Reserve"; Claude calls the `propose_card` tool (Day 16); the tool impl runs the Perplexity lookup and returns a `CardProposal`; React renders the proposal as a parse card in the chat; user taps "looks right" → `POST /cards/confirm` writes the row.

Both paths use the same Perplexity lookup and the same `POST /cards/confirm` endpoint. There is no standalone `AddCard.tsx` page after onboarding — "add via tameru ai" is the post-onboarding affordance (UX frames 18 and 20).

## Read first

- `DESIGN.md` §6.1 (Perplexity card lookup), §8.1 (`cards` schema — note `source_urls` field), §7.2 (`propose_card` tool shape).
- `UX_PROMPT.md` frames 4 (Add First Card — onboarding), 18 (Cards List), 20 (Cards Empty).
- `CLAUDE.md` invariant 8.

## Deliverables

### `app/integrations/perplexity.py`

- `lookup_card(card_name: str) -> CardLookupResult` — calls Sonar with a structured-output prompt: "Return JSON with `program`, `multipliers` (object: category → number), `annual_fee`, `issuer` for the credit card named '{card_name}'. Use only authoritative sources (NerdWallet, The Points Guy, US Credit Card Guide, the issuer's site). Return citations."
- Parses the response. If parse fails or confidence is low, returns `{needs_manual: true, raw_text}`.
- Logs to `ai_call_log` with `provider="perplexity", task_type="card_lookup"` via the user-JWT `log_ai_call` helper (Day 4, invariant 14). No service role.

### `app/routes/cards.py`

- `POST /cards/lookup` — body: `{name}`. Calls Perplexity, returns the proposed card data + citations. Used by the onboarding flow *and* by the `propose_card` tool internals (Day 16).
- **`POST /cards/confirm`** — body: `CardProposal` payload (network, last4, program, multipliers, annual_fee, source_urls, alias?). Inserts the row. Returns the created card. Called after "looks right" on either the onboarding screen or the chat parse card.
- **No `POST /cards` (direct write from a free-form user form).** The only commit path is `/confirm` after a proposal the user saw.
- `GET /cards` — list active cards.
- `PATCH /cards/{id}` — edit (used from the More → My Cards list, UX frame 18, tap-to-edit).
- `DELETE /cards/{id}` — soft delete (`active=false`). Swipe-left on a row (UX frame 19).

### Frontend — onboarding (UX frame 4)

`frontend/src/pages/AddFirstCard.tsx`:

- 2-step progress indicator (step 1 of 2, accent).
- Card search input (sunken, pill-shape) + 3 suggestion chips.
- On submit: spinner + "looking up multipliers…" then render the proposed card preview (same visual layout as the chat parse card) with multipliers as editable rows.
- "add card" primary (enabled once a name is searched and looked up) → `POST /cards/confirm`.
- Manual fallback path when `needs_manual: true`: editable blank form.
- "skip for now" tertiary link → proceeds to the next onboarding step.

### Frontend — post-onboarding

`frontend/src/pages/Cards.tsx` (UX frame 18):

- Top bar: back chevron + "my cards".
- List of card tiles. Each tile: colored left-edge stripe · card name · last-4 · program chip · multiplier chips.
- Swipe-left on a tile reveals the terracotta delete panel (UX frame 19).
- Tap tile → PATCH edit sheet.
- Empty state (UX frame 20): card icon + "no cards yet" + "add via tameru ai" primary (deep-links to the chat half-sheet pre-seeded with a "let's add your first card" suggestion chip).
- AI hint footer on the populated list: "✨ add a new card via tameru ai →" (taps into chat).

**There is no standalone `AddCard.tsx` page after onboarding.** The post-onboarding add path is chat-only. Day 16's `propose_card` tool is the entry point; Day 18's `ParseCard` component renders the preview.

### Tests

- `tests/test_cards.py`: mocked Perplexity responses; round-trip `lookup → confirm → saved row`. `POST /cards/confirm` validates the payload and rejects malformed proposals.
- RLS: user A cannot GET / PATCH / DELETE user B's cards.
- AI log check: one `ai_call_log` row per Perplexity call with correct `task_type`.

## Don't

- Don't pre-seed a card library. Perplexity is the source of truth at lookup time.
- Don't expose Perplexity's raw response to the user — parse to structured JSON.
- Don't store citation URLs as a single string; use the `text[]` column.
- Don't build a standalone `AddCard.tsx` page for post-onboarding use. Chat is the add surface after onboarding (invariant 8).
- Don't write to `cards` from inside `propose_card` (Day 16). The tool returns a proposal; `POST /cards/confirm` commits.

## Done when

- Onboarding: adding "Chase Sapphire Reserve" produces sane multipliers + ≥2 citations; user confirms; row exists in `cards`.
- Post-onboarding: saying "add my Amex Gold" in chat fires `propose_card`, renders a parse card in chat, user confirms, row is added, Cards list reflects it.
- Manual fallback path works on onboarding when Perplexity is uncertain.
- `ai_call_log` shows Perplexity calls with `task_type="card_lookup"`, written via the user-JWT path.
- Swipe-left on a card tile reveals the delete panel; confirming removes it.
