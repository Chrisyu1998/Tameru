# Day 5 — Transactions API (CRUD + merchant memory upsert)

## Goal

REST endpoints for transactions. POST suggests a category via Day 4's `categorize()`. PATCH on a category change upserts to `merchant_category`. List supports pagination and filters per `DESIGN.md` §6.2 transaction list UX.

## Read first

- `DESIGN.md` §6.2 (entry + list UX), §8.2 (transactions schema), §8.4 (merchant_category — most-recent-correction-wins).
- `CLAUDE.md` invariants 1, 7.

## Deliverables

- `app/routes/transactions.py`:
  - `POST /transactions` — body: `{merchant, amount, date?, card_id?, category?, notes?}`. If `category` is omitted, call `categorize()` and use its suggestion as `category` and store the raw suggestion in `gemini_suggestion`. Return the inserted row. `source = "manual"` by default; allow callers to override.
  - `GET /transactions` — query params: `card_id?, category?, date_from?, date_to?, search?, limit?=50, offset?=0`. Returns `{items, total, has_more}`. RLS-scoped (uses `supabase_for_user`).
  - `GET /transactions/{id}` — single row, RLS-scoped.
  - `PATCH /transactions/{id}` — partial update. **Special behavior:** if the body changes `category` from the prior value, upsert into `merchant_category` with `(user_id, merchant_normalized, category, updated_at=now())` using `ON CONFLICT (user_id, merchant) DO UPDATE`.
  - `DELETE /transactions/{id}` — hard delete, RLS-scoped.
- Merchant normalization helper: `normalize_merchant(s) -> s.strip().lower()`. Used in both `categorize()` lookup and `merchant_category` upsert.
- `tests/test_transactions.py`:
  - POST without category → row created with `gemini_suggestion` populated and `category` = suggestion.
  - PATCH category → `merchant_category` row exists, then PATCH again to a different category → same row updated, `updated_at` newer.
  - List: pagination boundaries, filter by category, search substring.
  - RLS still holds: user A cannot GET/PATCH/DELETE user B's transactions.

## Don't

- Don't add a `correction_count` column or logic — design dropped it (§0).
- Don't add subscription auto-logging here — Day 14 owns that.
- Don't expose the `gemini_suggestion` field as user-editable. Read-only.

## Done when

- `pytest tests/test_transactions.py` passes including the RLS sub-tests.
- A round-trip works: POST a transaction with no category → it gets one from Gemini → PATCH the category → the next POST for the same merchant uses the corrected category in its prompt's "past corrections" block.
