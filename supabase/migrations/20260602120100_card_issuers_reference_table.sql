-- card_issuers — Tier 3 (international cards) reference table. DESIGN.md §6.6.
--
-- Global (non-user) reference data mapping each `CardIssuer` enum key to its
-- region, a display name, and the issuer's web domain (used to widen the
-- card-lookup `allowed_domains` allowlist). This is the DB-side source of
-- truth for issuer→region routing; the backend mirrors it in Python
-- (app/integrations/card_regions.py) and the frontend mirrors the labels in
-- TS (cardsApi.ts), the same "both layers must agree" pattern the issuer
-- CHECK ↔ Pydantic Literal already follows. We deliberately do NOT add an FK
-- from cards.issuer → card_issuers.key: the widened CHECK on cards.issuer
-- already enforces membership, and keeping this table as pure metadata avoids
-- coupling the hot insert path to a second table.
--
-- region is NULL for 'other' (the escape-hatch issuer has no fixed region —
-- the confirm route falls back to deriving region from the user's
-- home_currency in that case).

CREATE TABLE card_issuers (
    key          text PRIMARY KEY,
    region       text CHECK (region IS NULL OR region IN ('US', 'JP', 'TW')),
    display_name text NOT NULL,
    domain       text
);

-- Reference data — readable by every signed-in user (and anon; issuer
-- names + public domains are not sensitive). No write policy: rows change
-- only via migrations / service_role, never through a user JWT.
ALTER TABLE card_issuers ENABLE ROW LEVEL SECURITY;

CREATE POLICY card_issuers_read ON card_issuers
    FOR SELECT
    USING (true);

INSERT INTO card_issuers (key, region, display_name, domain) VALUES
    -- US
    ('chase',           'US', 'Chase',              'chase.com'),
    ('amex',            'US', 'Amex',               'americanexpress.com'),
    ('citi',            'US', 'Citi',               'citi.com'),
    ('capital_one',     'US', 'Capital One',        'capitalone.com'),
    ('discover',        'US', 'Discover',           'discover.com'),
    ('bank_of_america', 'US', 'Bank of America',    'bankofamerica.com'),
    ('wells_fargo',     'US', 'Wells Fargo',        'wellsfargo.com'),
    ('usaa',            'US', 'USAA',               'usaa.com'),
    ('bilt',            'US', 'Bilt',               'biltrewards.com'),
    ('barclays',        'US', 'Barclays',           'barclaysus.com'),
    ('us_bank',         'US', 'U.S. Bank',          'usbank.com'),
    ('synchrony',       'US', 'Synchrony',          'synchrony.com'),
    -- JP
    ('rakuten',         'JP', 'Rakuten',            'rakuten-card.co.jp'),
    ('smbc',            'JP', 'SMBC',               'smbc-card.com'),
    ('jcb',             'JP', 'JCB',                'jcb.co.jp'),
    ('aeon',            'JP', 'AEON',               'aeon.co.jp'),
    ('epos',            'JP', 'Epos',               'eposcard.co.jp'),
    ('saison',          'JP', 'Saison',             'saisoncard.co.jp'),
    -- TW
    ('cathay',          'TW', 'Cathay United',      'cathaybk.com.tw'),
    ('esun',            'TW', 'E.SUN',              'esunbank.com.tw'),
    ('ctbc',            'TW', 'CTBC',               'ctbcbank.com'),
    ('taishin',         'TW', 'Taishin',            'taishinbank.com.tw'),
    ('fubon',           'TW', 'Fubon',              'fubon.com'),
    ('union',           'TW', 'Union Bank',         'ubot.com.tw'),
    -- escape hatch
    ('other',           NULL, 'Other',              NULL);
