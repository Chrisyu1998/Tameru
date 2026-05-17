-- Day 14 follow-up — correct the card-identity uniqueness invariant.
-- DESIGN.md §8.1 (cards schema + constraints).
--
-- The earlier index `(user_id, network, last_four)` (migration
-- 20260516130000_cards_network_and_deactivated_at.sql) was wrong for the
-- real-world case where two cards from DIFFERENT issuers share both
-- network and last 4 — e.g. Chase Sapphire (Visa) ending 1234 and
-- Capital One Venture (also Visa) ending 1234. Those are distinct cards;
-- the old index blocked the second add with a 409.
--
-- Correct invariant: card numbers are issued PER BANK, not per network.
-- A single issuer cannot give one person two cards with the same number;
-- different issuers absolutely can produce same-last_4 collisions. So
-- the proper tiebreaker is `issuer`, not `network`.
--
-- Issuer also becomes a closed-enum CHECK constraint so case variants
-- ("Chase" vs "chase") and friendly-name variants ("Amex" vs "American
-- Express") cannot create phantom duplicates that defeat the index.
-- The `other` value is the escape hatch for issuers we didn't enumerate.

-- Step 1: normalize any pre-existing free-form issuer values onto the
-- new enum BEFORE adding the CHECK. v1 prod has no card rows yet, but
-- conftest fixtures and local dev data may have title-case strings.
UPDATE cards SET issuer = LOWER(REPLACE(issuer, ' ', '_'));
UPDATE cards SET issuer = 'amex'
    WHERE issuer IN ('american_express', 'amex_card');
UPDATE cards SET issuer = 'citi'
    WHERE issuer = 'citibank';
UPDATE cards SET issuer = 'capital_one'
    WHERE issuer = 'capitalone';
UPDATE cards SET issuer = 'bank_of_america'
    WHERE issuer IN ('bofa', 'boa');
UPDATE cards SET issuer = 'other'
    WHERE issuer NOT IN (
        'chase','amex','citi','capital_one','discover',
        'bank_of_america','wells_fargo','usaa','bilt',
        'barclays','us_bank','synchrony','other'
    );

-- Step 2: enforce the enum at the DB layer. Application Pydantic models
-- enforce the same set; both layers must agree (a request that lands
-- here with an off-enum issuer means the Pydantic Literal check was
-- bypassed, which should be impossible).
ALTER TABLE cards
    ADD CONSTRAINT cards_issuer_check
    CHECK (issuer IN (
        'chase','amex','citi','capital_one','discover',
        'bank_of_america','wells_fargo','usaa','bilt',
        'barclays','us_bank','synchrony','other'
    ));

-- Step 3: replace the partial unique index. Same `WHERE active = true`
-- exemption so soft-deleted rows are exempt and re-add still creates
-- a fresh card_id (DESIGN.md §8.1 soft-delete semantics).
DROP INDEX IF EXISTS cards_active_identity_uniq;

CREATE UNIQUE INDEX cards_active_identity_uniq
    ON cards (user_id, issuer, last_four)
    WHERE active = true;
