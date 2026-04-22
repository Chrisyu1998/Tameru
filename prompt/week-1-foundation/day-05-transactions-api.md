# Day 5 — Transactions API (confirm + list + edit + delete + merchant memory upsert)

## Goal

REST endpoints for transactions supporting the chat-unified write surface: a **confirm** endpoint (invoked after the user taps "looks right" on a chat parse card), list and detail reads (powering the transaction list UX, frame 11a, and the agent's `get_transactions` tool), PATCH for the edit sheet (frame 11b), and DELETE from the edit sheet or swipe-on-list. No free-form "create from user form" endpoint — chat is the only user-initiated create path (CLAUDE.md invariant 8). The creation-from-chat flow is split between Day 9 (`propose_transaction` tool) and this day (the confirm + downstream writes).

The endpoints are UI-surface-agnostic: PATCH and DELETE accept any caller that presents an explicit user tap. v1 exercises them from the edit sheet and swipe-to-delete only; a post-launch inline-chat-confirm-card path (§6.2) would hit the same endpoints without changes. Design the endpoints so that forward path is not foreclosed, but do not implement the inline confirm card here or in Day 9/Day 10.

## Read first

- `DESIGN.md` §6.2 (chat-based entry flow + transaction list UX — both edit surfaces), §8.2 (transactions schema — note `client_request_id`, trust posture on `gemini_suggestion`), §8.4 (`merchant_category` — both upsert sites).
- `CLAUDE.md` invariants 1, 7, 8, 13, 14.

## Architecture

The chat write flow is: user message → Claude calls `propose_transaction(...)` tool (Day 9) → tool impl calls `categorize()` (Day 4), generates a fresh `client_request_id` UUID, and returns a `TransactionProposal` payload → React renders the parse card (UX frame 15) → user taps "looks right" → **`POST /transactions/confirm`** writes the row and returns it along with the Entry-Moment Insight (Day 13).

No row is ever written without the user's explicit UI confirmation. The confirm endpoint is the single commit point for chat-created transactions.

Edits and deletes use the same HTTP endpoints (`PATCH /transactions/{id}`, `DELETE /transactions/{id}`) from the edit sheet (Day 15). The endpoints are UI-agnostic: a post-launch inline chat confirm card (§6.2) would use the same endpoints without modification. What matters is that `tool_use` itself never commits (invariant 8).

## Deliverables

### `app/models/transactions.py` — shared Pydantic models

Create `TransactionProposal`, `TransactionConfirmRequest`, `TransactionRow`, `TransactionListResponse`, `TransactionPatchRequest` here. Day 9's `propose_transaction` tool imports `TransactionProposal` from this module — one source of truth, no shape drift between the tool and the endpoint.

`TransactionProposal` fields: `merchant: str`, `amount: Decimal`, `date: date`, `card_id: UUID | None`, `category: str`, `notes: str | None`, `gemini_suggestion: str | None`, `client_request_id: UUID`. No `source` — the server hardcodes `"nlp"` on the confirm path (see below).

### New migration — `client_request_id` on `transactions`

Add a migration under `supabase/migrations/` that:

- Adds a nullable `client_request_id UUID` column to `transactions`.
- Creates `CREATE UNIQUE INDEX transactions_user_client_request_id_unique ON transactions (user_id, client_request_id) WHERE client_request_id IS NOT NULL;`.

pg_cron's subscription auto-logger (Day 19) and CSV import (Day 20) leave this column `NULL` — the partial predicate excludes them from the uniqueness constraint. Update the schema block in `DESIGN.md` §8.2 is already done; this migration makes the schema match.

### `app/services/transactions.py` — shared query layer

Extract a single `list_transactions(user: AuthedUser, filters: TransactionFilters) -> TransactionListResponse` function here. The route handler in `app/routes/transactions.py` calls it. Day 9's `get_transactions` agent tool also calls it — **not** over HTTP, directly as a Python function. One query builder, two callers. Without this extraction, the agent tool and the list handler drift.

### `app/routes/transactions.py`

- **`POST /transactions/confirm`** — body: `TransactionConfirmRequest` (fields as in `TransactionProposal`). Behavior:
  1. Validate `category` is in the Day 4 closed enum (`ALLOWED_CATEGORIES`). 422 with a clear error code on miss.
  2. Validate `amount > 0`. 422 on miss.
  3. Validate `date <= today + 1 day` (one-day slack for timezone seams; far-future dates are pg_cron's job via SQL, not this endpoint's).
  4. If `card_id` is provided, validate it belongs to the authed user by reading `cards` through `supabase_for_user(user.jwt)`. 422 if not found (RLS on `cards` returns empty for another user's card). This stops a client from FK-linking to another user's card id — RLS on `transactions` alone doesn't prevent that (its `WITH CHECK` only verifies `user_id = auth.uid()`).
  5. **Idempotent insert keyed on `client_request_id`.** If a row already exists for `(user_id, client_request_id)`, return it with `insight: null` (a replayed confirm should not re-fire the entry-moment insight; the original client either already rendered it or has moved past it — see Day 15 offline queue). Otherwise insert with `source="nlp"` hardcoded server-side — the API body does not accept a `source` field.
  6. **Upsert `merchant_category` on override.** If `category != gemini_suggestion` (i.e. user fixed Gemini's guess at entry time), upsert `(user_id, normalize_merchant(merchant), category, updated_at=now())` via `ON CONFLICT (user_id, merchant) DO UPDATE`. This is the highest-signal correction moment — the fix to Day 4's prompt's past-corrections cache fires here, not only on PATCH. If `category == gemini_suggestion`, do **not** upsert — caching confirmations pollutes the prompt slot with redundant rows (DESIGN.md §8.4).
  7. Return `{transaction: TransactionRow, insight: str | null}`.

  **Day 13 stub:** `insight` is hardcoded `null` in this day's implementation. The field is in the response schema from day one so Day 10 (`EntryInsightBubble`) can treat the contract uniformly. Day 13 wires `entry_moment_insight()` into this endpoint in a one-line change.

  **Trust posture on `gemini_suggestion`** (pin, do not re-litigate): the field is accepted as-is from the client. A user tampering with it forges audit data only on their own account and gains nothing (invariant 14 logic). **Do not re-call `categorize()` to verify it** — that doubles Gemini cost on every confirm and defeats the propose-then-confirm split. Do not add HMAC signing of proposals. Do not introduce a server-side `transaction_proposals` table for v1. If override-rate analytics later become load-bearing (e.g. under the §17 scaling plan), revisit; not before.

  **There is no `POST /transactions` that accepts raw user-typed fields.** User-initiated creates go through chat → `propose_transaction` → `POST /transactions/confirm`. CSV import (Day 20) writes transactions at the SQL layer using `supabase_for_user`; the subscription auto-logger (Day 19) writes via pg_cron SQL. Neither goes through this endpoint.

- **`GET /transactions`** — query params: `card_id?, category?, merchant_contains?, date_from?, date_to?, amount_min?, amount_max?, limit?=50, offset?=0`. Returns `{items: TransactionRow[], has_more: bool}`. **No `total` field** — computing it requires a separate COUNT query and the UX (infinite scroll + agent disambiguation) only needs `has_more`. RLS-scoped via `supabase_for_user`. Order by `date DESC, created_at DESC` (matches the existing `transactions_user_date_idx`). This handler is a thin wrapper around `list_transactions()` from `app/services/transactions.py`.

  `limit` is clamped server-side to a max of 500 (the same hard cap Day 9's `get_transactions` tool relies on; see DESIGN.md §7.2.1). Powers both the list UX (frame 11a) and the agent's `get_transactions` tool.

- **`GET /transactions/{id}`** — single row, RLS-scoped.

- **`PATCH /transactions/{id}`** — partial update. Body: `TransactionPatchRequest` (any subset of `merchant`, `amount`, `date`, `card_id`, `category`, `notes`). Validation re-uses the confirm-path checks (enum, `amount > 0`, `date` bound, card ownership). Used by the edit sheet (UX frame 11b) in v1. Kept UI-agnostic so a post-launch inline chat update-confirm card (§6.2) can call it without changes.

  **`merchant_category` upsert on PATCH** (DESIGN.md §8.4, second site):
  - Fires only when the PATCH body contains `category` and the new value differs from the stored value.
  - Keyed on the **new** normalized merchant if the body also changed `merchant`; otherwise on the stored merchant.
  - A merchant-only PATCH (no `category` change) does **not** touch `merchant_category` — changing the display spelling isn't a category correction.

- **`DELETE /transactions/{id}`** — hard delete, RLS-scoped. Called from swipe-left on the list surface or the Delete button on the edit sheet in v1. The agent has no `delete_transaction` tool (invariant 8). A post-launch inline chat delete-confirm card (§6.2) would reuse this endpoint.

Reuse `normalize_merchant` from `app/util/merchant.py` (Day 4). Do not reimplement.

### Chat candidate-list shape

When the agent calls `list_transactions` (via the `get_transactions` tool) with disambiguation parameters (e.g. `merchant_contains="coffee"`, `amount_min=9, amount_max=11, date_from=<2 weeks ago>`), the response `items` shape is already what the React chat UI needs to render tappable candidate cards (frame 11b flow) for any match count, including a single-row result. No separate endpoint required.

### `tests/test_transactions.py`

- `POST /transactions/confirm` with a valid proposal → row created, `gemini_suggestion` preserved if provided, `category` preserved, `source="nlp"` set by the server, returns `{transaction, insight: null}`.
- `POST /transactions/confirm` with `category` not in the Day 4 closed enum → 422 with a clear error code.
- `POST /transactions/confirm` with `amount <= 0` → 422.
- `POST /transactions/confirm` with `date > today + 1 day` → 422.
- `POST /transactions/confirm` where `card_id` belongs to another user → 422 (card ownership validation).
- `POST /transactions/confirm` where `category != gemini_suggestion` → row created AND `merchant_category` row upserted for `(user_id, normalize_merchant(merchant), category)`. A subsequent `categorize()` call for the same merchant sees the corrected category in its past-corrections block.
- `POST /transactions/confirm` where `category == gemini_suggestion` → row created, `merchant_category` **not** touched.
- `POST /transactions/confirm` replayed with the same `client_request_id` (simulating offline queue drain after reconnect) → no duplicate row; response is `{transaction: <original row>, insight: null}`.
- `POST /transactions/confirm` with a body missing `source` succeeds (server sets `"nlp"`); with a body including `source` — 422 or silently ignored (pick one and make it consistent). Server-hardcoded source is the stance.
- `PATCH` changing `category` to a new value → `merchant_category` upserted; PATCH again to a different category → same row updated, `updated_at` newer.
- `PATCH` changing `merchant` only (no `category` in body) → `merchant_category` untouched.
- `PATCH` changing both `merchant` and `category` → upsert keyed on the new normalized merchant.
- `GET /transactions` filter combinations: `merchant_contains` substring, `amount_min/max` bounds, date range, category — each exercised independently. Response shape is `{items, has_more}` (no `total`).
- `GET /transactions` pagination boundaries: `offset` past the end returns empty `items` with `has_more=false`.
- `GET /transactions` ordering: results sorted by `date DESC, created_at DESC`.
- `GET /transactions` `limit` clamping: request with `limit=10000` returns at most 500 rows.
- RLS: user A cannot GET / PATCH / DELETE user B's transactions.
- Service-layer parity: `app/services/transactions.py::list_transactions(user, filters)` and `GET /transactions` return identical payloads for the same filters. This is the safety net against Day 9 drifting from the HTTP shape.

## Don't

- Don't add a `POST /transactions` that accepts raw user-typed merchant/amount/etc. directly. The chat path creates via propose → confirm; no form-submit create path exists in v1.
- Don't add a `correction_count` column or logic — design dropped it (§0).
- Don't add subscription auto-logging here — Day 19 owns that (pg_cron SQL function, not HTTP).
- Don't accept `source` on the confirm body. The server hardcodes `"nlp"`. CSV import (Day 20) and pg_cron (Day 19) write at the SQL layer with their own `source` values.
- Don't expose the `gemini_suggestion` field as user-editable. Read-only.
- Don't re-call `categorize()` inside the confirm endpoint to "verify" `gemini_suggestion`. See Trust posture above.
- Don't add currency fields or conversion. Amounts are always in the user's `users_meta.home_currency` (invariant 13).
- Don't return `total` in the list response. `has_more` is sufficient for infinite scroll and agent disambiguation.
- Don't duplicate the query builder between the route handler and Day 9's `get_transactions` tool. Both go through `list_transactions()` in `app/services/transactions.py`.
- Don't foreclose chat-triggered PATCH/DELETE. The endpoints are called from any UI surface that presents an explicit user tap (edit sheet, swipe, inline confirm card). The architectural invariant is that `tool_use` itself doesn't commit (invariant 8), not that only the edit sheet may mutate.
- Don't use `supabase_admin()` in any handler in this file (invariant 1).

## Done when

- `pytest tests/test_transactions.py` passes including the RLS sub-tests, the closed-enum validation, the `client_request_id` idempotency test, the card-ownership test, and the confirm-time `merchant_category` upsert test.
- A round-trip works: Claude's `propose_transaction` tool (Day 9, mockable here) returns a proposal with `client_request_id` → `POST /transactions/confirm` writes the row, upserts `merchant_category` if the user edited Gemini's category, returns `{transaction, insight: null}` → PATCH the category via the edit sheet path → the next `propose_transaction` call for the same merchant reflects the corrected category in Day 4's prompt's "past corrections" block.
- Replaying the same `POST /transactions/confirm` (same `client_request_id`) returns the original row with no duplicate inserted.
- A `merchant_contains="coffee"` filter against `list_transactions()` returns all matches in the same shape whether called from the `GET /transactions` route handler or from a Python caller (Day 9's agent tool).
