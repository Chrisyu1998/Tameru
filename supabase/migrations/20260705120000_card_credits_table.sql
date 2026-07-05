-- card_credits — per-card statement-credit / perk tracker (DESIGN.md §6.7, §8.17)
--
-- Phase 1 of the credit-tracking feature: a manual tracker for the recurring
-- "spend $X, get $Y back" benefits on premium cards (Amex Platinum's $75/qtr
-- Lululemon, $100/qtr Resy, etc.). This is the annual-fee-tracking pattern
-- generalized — a *card consequence* that lives on the card, reuses the card
-- web_search lookup, a pg_cron reset in the auto-logger shape, and
-- propose-then-confirm. Invariant 8 is not implicated (an auxiliary table, not
-- a ledger table — same standing as goals / user_memory).
--
-- Schema follows the §8 status-column doctrine (`status` active|archived, no
-- `deleted`) and the `cards` crid pattern (§8.1): `client_request_id` is the
-- propose-confirm join key + idempotency guard.
--
-- `amount` is NULLABLE by design — the lookup fails closed to null when the
-- credit's amount is quoted in a currency ≠ home_currency (invariant 13, same
-- rule as the annual-fee prompt); the user then types it. `used_amount` is
-- NOT NULL DEFAULT 0. `current_period_start` / `next_reset_date` are NOT NULL:
-- every insert path (the confirm upsert RPC, manual add) seeds them from
-- `credit_period_bounds()` (migration 20260705120100), and the reset sweep
-- keys on `next_reset_date <= today`, so a NULL there would silently never
-- reset — fail-loud is safer.
--
-- card_id ON DELETE CASCADE (not RESTRICT): both `cards` and `card_credits`
-- cascade from auth.users on account deletion, and Postgres does not guarantee
-- sibling-cascade order — RESTRICT would break account teardown if `cards` is
-- deleted first (same reasoning as subscriptions.card_id, §8.3). Cards are
-- soft-deleted in normal operation (a status flip, no SQL DELETE), so the
-- companion-credit archive is handled explicitly in soft_delete_card
-- (migration 20260705120400), not by this cascade.

CREATE TABLE card_credits (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    card_id               uuid        NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    name                  text        NOT NULL,
    amount                numeric,                       -- per-period allowance; null = fail-closed / not yet set
    cadence               text        NOT NULL
                                      CHECK (cadence IN ('monthly', 'quarterly', 'semiannual', 'annual')),
    used_amount           numeric     NOT NULL DEFAULT 0,
    current_period_start  date        NOT NULL,          -- calendar-derived, seeded by credit_period_bounds()
    next_reset_date       date        NOT NULL,          -- next calendar boundary; the reset sweep's trigger
    merchant_hint         text,                          -- lowercased token for the Phase-2 ledger bridge
    status                text        NOT NULL DEFAULT 'active'
                                      CHECK (status IN ('active', 'archived')),
    source_urls           text[],                        -- web_search citations from the lookup
    verified_at           timestamptz,                   -- when the lookup last confirmed terms; "last checked"
    client_request_id     uuid        NOT NULL DEFAULT gen_random_uuid(),
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- Dedups the confirm under a race — same shape as cards_active_client_request_id_unique
-- (§8.1). A same-crid replay (offline-queue drain) can't create a second row.
CREATE UNIQUE INDEX card_credits_active_crid_uniq
    ON card_credits (user_id, client_request_id)
    WHERE status = 'active';

-- Soft natural key so re-running the lookup or a double-tap doesn't create two
-- "$75 Lululemon" rows on one card. Archived rows are exempt (re-add after
-- "stop tracking"). This is the arbiter the confirm upsert (migration
-- 20260705120200) targets with ON CONFLICT DO NOTHING: both a crid replay
-- (same names) and a re-lookup (new crids, same names) collide here. It's an
-- expression + partial index, so any ON CONFLICT write MUST go through a
-- plpgsql upsert that emits the matching predicate (PostgREST can't infer
-- expression/partial indexes — memory.md 2026-05-17 / 2026-05-19).
CREATE UNIQUE INDEX card_credits_active_name_uniq
    ON card_credits (card_id, lower(name))
    WHERE status = 'active';

-- List/read hot path: GET /card-credits?card_id= reads a card's active credits.
CREATE INDEX card_credits_card_status_idx
    ON card_credits (card_id, status);

ALTER TABLE card_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE card_credits FORCE  ROW LEVEL SECURITY;

-- Owner-only. All app writes (confirm, usage edits, archive) run under the
-- user's JWT; only the pg_cron reset_card_credits() sweep uses the service
-- role (which carries BYPASSRLS), matching the auto-logger's standing
-- (CLAUDE.md invariants 1 and 4).
--
-- INSERT / UPDATE additionally require the referenced card_id to be one of the
-- caller's OWN cards, not just any card in the system. Without this, a direct
-- PostgREST caller (the anon key + a valid user JWT both ship in the PWA
-- bundle) could bypass the confirm route and attach a credit row — with their
-- own user_id but another user's card_id — creating a cross-tenant FK
-- attachment and a card-id existence oracle (the FK only fails if the UUID
-- matches no card at all). The route/RPC already filter to owned active cards,
-- but per invariant 1 the DATABASE — not app code — is the tenant-isolation
-- boundary, so the check lives here too (defense in depth for the direct-PostgREST
-- path). Ownership is checked, not active-status: a credit may legitimately
-- outlive its card's soft-delete until the soft_delete_card cascade archives it.
CREATE POLICY card_credits_select ON card_credits
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY card_credits_insert ON card_credits
    FOR INSERT
    WITH CHECK (
        user_id = auth.uid()
        AND EXISTS (
            SELECT 1 FROM cards c
             WHERE c.id = card_credits.card_id
               AND c.user_id = auth.uid()
        )
    );

CREATE POLICY card_credits_update ON card_credits
    FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (
        user_id = auth.uid()
        AND EXISTS (
            SELECT 1 FROM cards c
             WHERE c.id = card_credits.card_id
               AND c.user_id = auth.uid()
        )
    );

CREATE POLICY card_credits_delete ON card_credits
    FOR DELETE
    USING (user_id = auth.uid());
