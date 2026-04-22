# Day 5 — Transactions API (confirm + list + edit + delete + merchant memory upsert)

## Goal

REST endpoints for transactions supporting the chat-unified write surface: a **confirm** endpoint (invoked after the user taps "looks right" on a chat parse card), list and detail reads (powering the transaction list UX, frame 11a), PATCH for the edit sheet (frame 11b), and DELETE. No free-form "create from user form" endpoint — chat is the only user-initiated create path (CLAUDE.md invariant 8). The creation-from-chat flow is split between Day 16 (`propose_transaction` tool) and this day (the confirm + downstream writes).

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow + transaction list UX), §8.2 (transactions schema), §8.4 (merchant_category — most-recent-correction-wins).
- `CLAUDE.md` invariants 1, 7, 8, 13, 14.

## Architecture

The chat write flow is: user message → Claude calls `propose_transaction(...)` tool (Day 16) → tool impl calls `categorize()` (Day 4) and returns a `TransactionProposal` payload → React renders the parse card (UX frame 15) → user taps "looks right" → **`POST /transactions/confirm`** writes the row and returns it along with the Entry-Moment Insight (Day 13).

No row is ever written without the user's explicit UI confirmation. The confirm endpoint is the single point of commit for transactions created from chat.

## Deliverables

### `app/routes/transactions.py`

- **`POST /transactions/confirm`** — body: `TransactionProposal` payload (merchant, amount, date, card_id, category, notes?, source="manual"|"nlp"|"auto_logged"|"csv_import", gemini_suggestion?). Validates against the closed category enum. Inserts the row. Returns `{transaction: Transaction, insight: str | null}` where `insight` comes from Day 13's deterministic entry-moment insight function.
  - `source` defaults to `"nlp"` when the chat path calls it; other writers (CSV import Day 6, subscription auto-logger Day 14) pass their own source.
  - **There is no `POST /transactions` that accepts raw user-typed fields.** User-initiated creates go through chat → `propose_transaction` → `POST /transactions/confirm`.
- `GET /transactions` — query params: `card_id?, category?, merchant_contains?, date_from?, date_to?, amount_min?, amount_max?, limit?=50, offset?=0`. Returns `{items, total, has_more}`. RLS-scoped (uses `supabase_for_user`). Powers both the list UX (frame 11a) and the agent's `get_transactions` tool (Day 16).
- `GET /transactions/{id}` — single row, RLS-scoped.
- `PATCH /transactions/{id}` — partial update. Used by the edit sheet (UX frame 11b). **Special behavior:** if the body changes `category` from the prior value, upsert into `merchant_category` with `(user_id, normalize_merchant(merchant), category, updated_at=now())` using `ON CONFLICT (user_id, merchant) DO UPDATE`. This is how the "most recent correction wins" contract from §8.4 gets populated.
- `DELETE /transactions/{id}` — hard delete, RLS-scoped.

Reuse `normalize_merchant` from `app/util/merchant.py` (Day 4). Do not reimplement.

### Chat candidate-list shape

When the agent calls `get_transactions` with ambiguity parameters (e.g. `merchant_contains="coffee"`, `amount_min=9, amount_max=11, date_from=<2 weeks ago>`), the response shape is already what the React chat UI needs to render tappable candidate cards (frame 11b flow). No separate endpoint required.

### `tests/test_transactions.py`

- `POST /transactions/confirm` with a valid proposal → row created with `gemini_suggestion` preserved if provided, `category` preserved, returns `{transaction, insight}`.
- `POST /transactions/confirm` with `category` not in the Day 4 closed enum → 422 with a clear error code.
- `PATCH` changing `category` → `merchant_category` row upserted; PATCH again to a different category → same row updated, `updated_at` newer.
- `GET /transactions` filter combinations: `merchant_contains` substring, `amount_min/max` bounds, date range, category — each exercised independently.
- Pagination boundaries: `offset` past the end returns empty `items` with `has_more=false`.
- RLS: user A cannot GET/PATCH/DELETE user B's transactions.

## Don't

- Don't add a `POST /transactions` that accepts raw user-typed merchant/amount/etc. directly. The chat path creates by confirming a proposal; no form-submit create path exists in v1. If another backend flow needs to insert (CSV, subscription auto-logger), it uses `POST /transactions/confirm` with its own `source` or writes directly at the SQL layer for service-role callers.
- Don't add a `correction_count` column or logic — design dropped it (§0).
- Don't add subscription auto-logging here — Day 14 owns that.
- Don't expose the `gemini_suggestion` field as user-editable. Read-only.
- Don't add currency fields or conversion. Amounts are always in the user's `users_meta.home_currency` (invariant 13).

## Done when

- `pytest tests/test_transactions.py` passes including the RLS sub-tests and the closed-enum validation.
- A round-trip works: Claude's `propose_transaction` tool (Day 16, mockable here) returns a proposal → `POST /transactions/confirm` writes the row → PATCH the category via the edit sheet path → the next `propose_transaction` call for the same merchant reflects the corrected category in Day 4's prompt's "past corrections" block.
- A `merchant_contains="coffee"` filter returns all matches for the chat disambiguation case.
