# Day 11 — Card management + Perplexity Sonar multiplier lookup

## Goal

User adds a card by name. Backend calls Perplexity Sonar to fetch program, multipliers, and annual fee with citations. User reviews and confirms in the UI. Card saved with citation URLs.

## Read first

- `DESIGN.md` §6.1 (Perplexity card lookup), §8.1 (`cards` schema — note `source_urls` field).

## Deliverables

- Backend:
  - `app/integrations/perplexity.py`:
    - `lookup_card(card_name: str) -> CardLookupResult` — calls Sonar with a structured-output prompt like: "Return JSON with `program`, `multipliers` (object: category → number), `annual_fee`, `issuer` for the credit card named '{card_name}'. Use only authoritative sources (NerdWallet, The Points Guy, US Credit Card Guide, the issuer's site). Return citations."
    - Parses the response. If parse fails or confidence is low, returns `{needs_manual: true, raw_text}`.
    - Logs to `ai_call_log` with `provider="perplexity", task_type="card_lookup"`.
  - `app/routes/cards.py`:
    - `POST /cards/lookup` — body: `{name}`. Calls Perplexity, returns the proposed card data + citations.
    - `POST /cards` — body: full card data confirmed by user. Inserts into `cards` with `source_urls` populated.
    - `GET /cards`, `PATCH /cards/{id}`, `DELETE /cards/{id}` (soft delete: set `active=false`).
  - `tests/test_cards.py`: mocked Perplexity responses; round-trip lookup → confirm → saved row.
- Frontend:
  - `frontend/src/pages/AddCard.tsx`:
    - Text input for card name. On submit: spinner + "Looking up multipliers…"
    - Show the proposed result: program, multipliers as editable rows (category, multiplier — user can edit any row), annual fee, citations as clickable links.
    - "Save card" button → `POST /cards`.
    - Manual fallback path (when Perplexity returns `needs_manual: true`): blank form to fill in.
  - `frontend/src/pages/Cards.tsx`: list of cards with multipliers visible; tap to edit.

## Don't

- Don't pre-seed a card library. Perplexity is the source of truth at lookup time.
- Don't expose Perplexity's raw response to the user — parse to structured JSON.
- Don't store the citation URLs as a single string; use the `text[]` column.

## Done when

- Adding "Chase Sapphire Reserve" produces sane multipliers and at least 2 citations.
- The user can edit any multiplier row before saving.
- Manual fallback path works when Perplexity is uncertain.
- `ai_call_log` shows the Perplexity call with `task_type="card_lookup"`.
