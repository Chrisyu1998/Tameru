-- Day 14 — cards: network, deactivated_at, partial unique identity index.
-- DESIGN.md §8.1 (cards schema), §6.1 (card lookup flow).
--
-- `network` is what disambiguates two cards of the same product (a user can
-- legitimately have two Amex Platinums — different last 4 distinguishes them,
-- but the `network` is what makes the (network, last_four) tuple meaningful
-- across issuers). Combined with `last_four` and gated on `active = true`,
-- it powers the active-identity uniqueness index below.
--
-- `deactivated_at` is set by the soft-delete handler when `active` flips to
-- false. Powers the "closed {MMM YYYY}" label on inactive rows in the
-- spending-breakdown filter; NULL for active rows.
--
-- Partial unique index: prevents two active cards with the same
-- (user_id, network, last_four). Inactive (soft-deleted) rows are deliberately
-- exempt so users can re-add a card after deleting it — see §8.1
-- "Soft-delete / re-add semantics" for the rationale (insert new row, do not
-- revive).

ALTER TABLE cards
    ADD COLUMN network        text,
    ADD COLUMN deactivated_at timestamptz;

-- Backfill: any pre-existing rows (none in v1 prod yet, but tests may have
-- created fixtures via card_a/card_b in conftest.py without a network value)
-- get 'other' so the NOT NULL + CHECK can land cleanly.
UPDATE cards SET network = 'other' WHERE network IS NULL;

ALTER TABLE cards
    ALTER COLUMN network SET NOT NULL,
    ADD CONSTRAINT cards_network_check
        CHECK (network IN ('visa', 'mastercard', 'amex', 'discover', 'other'));

CREATE UNIQUE INDEX cards_active_identity_uniq
    ON cards (user_id, network, last_four)
    WHERE active = true;
