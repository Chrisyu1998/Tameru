-- Day 15 follow-up — add `client_request_id` to cards as a stable
-- proposal join key. DESIGN.md §8.1.
--
-- WHY THIS EXISTS (not an idempotency token):
--
-- Idempotency for cards is already enforced structurally by the partial
-- unique index `cards_active_identity_uniq` on `(user_id, issuer,
-- last_four) WHERE status = 'active'`. A duplicate confirm hits Postgres
-- 23505 → the route surfaces 409 `active_card_exists` and the drain
-- treats it as silent dequeue. That dedup is still load-bearing and
-- untouched by this migration.
--
-- What was MISSING was a stable per-proposal identifier the chat
-- rehydrate pipeline could join on. The Day-15 annotation matched
-- `tameru_proposal` blocks to `cards` rows by `name` — fine when each
-- card name is unique within a user, broken when a user holds two
-- active cards with the same product name but different last 4 (e.g.,
-- "Amex Gold" 1234 and "Amex Gold" 5678 — perfectly legal under the
-- natural-key index since `last_four` distinguishes them). With name
-- as the only match key, both proposal blocks in chat history would
-- get annotated pointing at whichever row the join returned first.
-- Symmetric problem in the offline-queue drain handler's in-memory
-- patch (`_findCardParseTarget` in chatStore.ts).
--
-- `client_request_id` (named for consistency with the equivalent
-- column on `transactions`) is a UUID generated server-side at
-- `propose_card` time and persisted on:
--   • the `tameru_proposal` block's `result.client_request_id`
--     (via the existing `_persist_turn` augmentation path);
--   • the queued confirm body in the offline queue's IndexedDB;
--   • this column on the `cards` row at `/cards/confirm` time.
--
-- Annotation joins on it, the drain matches on it. 1:1 with the
-- original proposal — no ambiguity even with two same-name rows.
--
-- BACKFILL / DEFAULT:
--
-- Pre-existing rows (test fixtures + ~handful of dev-stack inserts —
-- v1 hasn't launched, no production users yet) get a fresh UUID via
-- the column DEFAULT. Their crid won't match any persisted proposal
-- block, so the chat rehydrate annotation falls back to name-based
-- matching for those rows. New proposals always carry a real crid.
--
-- IDEMPOTENCY ON `/cards/confirm`:
--
-- The route is being updated to accept `client_request_id` in the
-- request body and short-circuit on same-crid replay (return the
-- existing row 200, mirroring the `/transactions/confirm` idempotency
-- semantics). The natural-key 409 path stays as the fallback for
-- "different crid, same physical card" — i.e., a user who proposed
-- the same card twice in two separate chat turns and tried to commit
-- both. The 409 still surfaces; the drain still dequeues silently.

ALTER TABLE cards
    ADD COLUMN client_request_id uuid NOT NULL DEFAULT gen_random_uuid();

-- Partial unique index lets a soft-deleted row free up its slot, so a
-- re-add (which mints a fresh crid) doesn't collide with a stale
-- inactive row. Mirrors the same `WHERE status = 'active'` predicate
-- as `cards_active_identity_uniq`.
CREATE UNIQUE INDEX cards_active_client_request_id_unique
    ON cards (user_id, client_request_id)
    WHERE status = 'active';
