-- card_credit_history — closed-period usage snapshots (DESIGN.md §6.7, §8.18)
--
-- Phase 2 of the credit-tracking feature. Optional period snapshots so the
-- Credits page can show "last quarter you used $60 / $75." One row is written
-- by the reset_card_credits() sweep (migration 20260705120800) at each period
-- rollover, capturing the CLOSING period's used_amount + amount + bounds right
-- before used_amount is zeroed and the period advances.
--
-- Phase 1 shipped the reset sweep with a documented "snapshot goes here" gap;
-- this migration adds the table and the same migration's sibling (120800)
-- fills the gap. Until this table exists the sweep simply skips the snapshot.
--
-- Read-only to the user: the table has ONLY a SELECT policy. Every write comes
-- from reset_card_credits() (SECURITY DEFINER → runs as the definer, bypassing
-- RLS), the same standing as any pg_cron-written row. A user JWT can read its
-- own history and nothing else; there is no user INSERT/UPDATE/DELETE path, so
-- a compromised JWT cannot forge or scrub history (same posture as ai_call_log,
-- CLAUDE.md invariant 14).
--
-- name / amount are snapshotted (not read live off card_credits) so a later
-- rename or amount edit does not rewrite what the closed period actually was —
-- history is immutable fact, the live row is current state.

CREATE TABLE card_credit_history (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    card_credit_id   uuid        NOT NULL REFERENCES card_credits(id) ON DELETE CASCADE,
    name             text        NOT NULL,          -- snapshot of the credit name at rollover
    amount           numeric,                       -- the closed period's allowance (null = never set)
    used_amount      numeric     NOT NULL,          -- how much was used in the closed period
    period_start     date        NOT NULL,          -- the closed period's start
    period_end       date        NOT NULL,          -- the boundary that fired (= the new period's start)
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Idempotent snapshot: the reset advances next_reset_date past today after the
-- first fire, so a same-day rerun is a no-op on card_credits — and this unique
-- key makes the snapshot INSERT a no-op too (ON CONFLICT DO NOTHING) even if
-- the sweep somehow re-processes the same closing period.
CREATE UNIQUE INDEX card_credit_history_credit_period_uniq
    ON card_credit_history (card_credit_id, period_start);

-- Read hot path: the Credits page fetches a credit's most recent closed period
-- ("last quarter you used $X"), newest first.
CREATE INDEX card_credit_history_credit_period_idx
    ON card_credit_history (card_credit_id, period_start DESC);

ALTER TABLE card_credit_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE card_credit_history FORCE  ROW LEVEL SECURITY;

-- SELECT-only for the owner. No INSERT/UPDATE/DELETE policies: the reset sweep
-- (SECURITY DEFINER) is the sole writer, and FORCE RLS + no write policy means
-- any user-JWT write is denied even though the default-privilege backfill
-- (20260515210000) granted authenticated table DML.
CREATE POLICY card_credit_history_select ON card_credit_history
    FOR SELECT
    USING (user_id = auth.uid());
