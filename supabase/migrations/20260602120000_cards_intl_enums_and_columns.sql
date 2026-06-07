-- Tier 3 (international cards) — Day 30. DESIGN.md §6.6, TODO.md.
--
-- Widens the closed-enum CHECKs on `cards.network` and `cards.issuer` to
-- cover Japan + Taiwan, and adds the three columns the JP/TW reward model
-- needs. Per the resolved scope decision (memory.md 2026-06-02):
--
--   * Network stays an enum (the global card-network universe is ~6) — we
--     just add `jcb` (dominant in JP, common in TW) and `diners`.
--   * Issuer stays an enum (the Pydantic `CardIssuer` Literal mirrors it,
--     and the `(user_id, issuer, last_four)` partial unique index relies on
--     a normalized key) — we widen it with the top JP + TW issuers rather
--     than replacing the CHECK with a free-text + reference-table lookup.
--     The companion `card_issuers` reference table (next migration) carries
--     region/domain/display metadata keyed by these same values.
--   * `region` ('US'|'JP'|'TW') makes reward lookup route per card — a US
--     card and a TW card can live in one wallet and each routes to its own
--     sources (the "moved back to Taiwan, still carries US cards" case).
--   * `base_reward_rate` + `rewards_currency` hold the JP/TW reward shape:
--     outside the US we capture a base earn rate + a free-text rewards
--     label (e.g. "Rakuten Points", "現金回饋"), never category multipliers
--     (those are partner-economy / user-selected / mobile-pay driven and a
--     one-shot, no-refresh lookup can't represent them stably).
--
-- Amounts stay single-home-currency (invariant 13 unchanged): there is no
-- per-card currency here. `base_reward_rate` is a percentage, not money.

-- Network: add jcb + diners.
ALTER TABLE cards DROP CONSTRAINT cards_network_check;
ALTER TABLE cards
    ADD CONSTRAINT cards_network_check
        CHECK (network IN (
            'visa', 'mastercard', 'amex', 'discover', 'jcb', 'diners', 'other'
        ));

-- Issuer: add the top ~6 JP and ~6 TW issuers. Keys are snake_case,
-- hyphen-free (the chat `card_ref` handle splits on the LAST hyphen as
-- `{issuer}-{last_four}` — an issuer key containing a hyphen would break
-- that resolution; none below do). The Pydantic `CardIssuer` Literal in
-- app/models/cards.py mirrors this exact set — both layers must agree.
ALTER TABLE cards DROP CONSTRAINT cards_issuer_check;
ALTER TABLE cards
    ADD CONSTRAINT cards_issuer_check
        CHECK (issuer IN (
            -- US
            'chase','amex','citi','capital_one','discover',
            'bank_of_america','wells_fargo','usaa','bilt',
            'barclays','us_bank','synchrony',
            -- JP
            'rakuten','smbc','jcb','aeon','epos','saison',
            -- TW
            'cathay','esun','ctbc','taishin','fubon','union',
            'other'
        ));

-- Region: which source set / reward model a card's lookup uses. NOT NULL
-- with a US default so the 13 pre-existing US issuers and any existing
-- rows backfill cleanly; the confirm route always sets it explicitly
-- going forward (derived from the issuer, falling back to home_currency).
ALTER TABLE cards
    ADD COLUMN region text NOT NULL DEFAULT 'US'
        CHECK (region IN ('US', 'JP', 'TW'));

-- Base earn rate (percent, e.g. 1.0 for 1%) + free-text rewards label.
-- Populated by the JP/TW base-rate lookup path; null on US cards (which
-- use `multipliers` instead). base_reward_rate is a rate, not money, but
-- numeric keeps it exact and consistent with the no-float house rule.
ALTER TABLE cards
    ADD COLUMN base_reward_rate numeric
        CHECK (base_reward_rate IS NULL OR base_reward_rate >= 0),
    ADD COLUMN rewards_currency text;
