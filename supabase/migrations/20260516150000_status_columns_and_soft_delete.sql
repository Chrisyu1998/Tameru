-- Status-column doctrine + transactions soft-delete — DESIGN.md §8 (preamble).
--
-- Three changes in one migration:
--
-- 1. `cards.active boolean` → `cards.status text` (CHECK 'active' | 'deleted').
--    Rename `deactivated_at` → `deleted_at` for symmetry with the new
--    transactions column. Partial unique index re-targets `WHERE status =
--    'active'`. RLS and constraints carry forward.
--
-- 2. `transactions` gains `status text` + `deleted_at timestamptz`. The
--    DELETE endpoint now soft-deletes by UPDATE-ing these columns; reads
--    target a new `active_transactions` view that filters them out. The two
--    partial unique indexes (`(subscription_id, date)` for pg_cron
--    idempotency, `(user_id, client_request_id)` for chat-confirm
--    idempotency) re-target `WHERE status = 'active'` so deleted rows do not
--    occupy the unique slot (re-add after delete creates a fresh row, same
--    as the cards re-add doctrine in §8.1).
--
-- 3. `dashboard_summary(date)`, `entry_moment_signals(uuid)`, and
--    `top_user_merchants` are CREATE OR REPLACE-d to filter deleted rows.
--    SQL function call sites stay byte-identical from the Python layer.
--
-- The `active_transactions` view exposes the soft-delete filter to PostgREST
-- with `security_invoker = true` so RLS still enforces `auth.uid() = user_id`
-- against the caller's JWT. Application read paths target the view; the few
-- sites that must see deleted rows (chat rehydrate annotation, audit) opt
-- into the base table explicitly.
--
-- v1 has ~10 users and limited data, so the backfill is trivial: every
-- existing card row sets `status = 'active'` (its prior `active = true`
-- value); every existing transaction row sets `status = 'active'`.

-- ---------------------------------------------------------------------------
-- cards: active boolean → status text, rename deactivated_at → deleted_at
-- ---------------------------------------------------------------------------

ALTER TABLE cards
    ADD COLUMN status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deleted'));

-- Backfill from the prior boolean. At v1 scale this touches ~0–dozens of
-- rows; an explicit UPDATE keeps the intent visible in the migration log.
UPDATE cards SET status = CASE WHEN active THEN 'active' ELSE 'deleted' END;

-- Drop the partial unique index BEFORE dropping the column it references.
DROP INDEX IF EXISTS cards_active_identity_uniq;

ALTER TABLE cards DROP COLUMN active;

ALTER TABLE cards RENAME COLUMN deactivated_at TO deleted_at;

-- Recreate the partial unique index against the new column. Same shape as
-- before (DESIGN.md §8.1) — issuer is the tiebreaker; deleted rows are
-- exempt so re-add creates a fresh card_id.
CREATE UNIQUE INDEX cards_active_identity_uniq
    ON cards (user_id, issuer, last_four)
    WHERE status = 'active';


-- ---------------------------------------------------------------------------
-- transactions: add status + deleted_at; re-scope partial unique indexes
-- ---------------------------------------------------------------------------

ALTER TABLE transactions
    ADD COLUMN status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deleted')),
    ADD COLUMN deleted_at timestamptz;

-- Replace the two partial unique indexes so they only consider active rows.
-- Deleted rows do not occupy unique slots — see DESIGN.md §8.2 partial-index
-- rationale for why this is defense-in-depth around the chat rehydrate UX
-- and the (rare) pg_cron auto-logger re-fire case.
--
-- `transactions_subscription_date_unique` was installed by 20260421120300_transactions.sql.
-- `transactions_user_client_request_id_unique` was installed by 20260422120000_transactions_client_request_id.sql.
-- Both get dropped and re-created with the status-aware predicate.
DROP INDEX IF EXISTS transactions_subscription_date_unique;
DROP INDEX IF EXISTS transactions_user_client_request_id_unique;

CREATE UNIQUE INDEX transactions_subscription_date_unique
    ON transactions (subscription_id, date)
    WHERE status = 'active' AND subscription_id IS NOT NULL;

CREATE UNIQUE INDEX transactions_user_client_request_id_unique
    ON transactions (user_id, client_request_id)
    WHERE status = 'active' AND client_request_id IS NOT NULL;


-- ---------------------------------------------------------------------------
-- active_transactions view — default-safe read surface for app code.
-- ---------------------------------------------------------------------------

-- `security_invoker = true` is load-bearing: without it, the view runs as
-- its owner (SECURITY DEFINER semantics) and would bypass the per-user RLS
-- policy on `transactions`. With it, the caller's JWT scopes the read and
-- `auth.uid() = user_id` fires identically to a direct base-table select.
CREATE OR REPLACE VIEW active_transactions
    WITH (security_invoker = true) AS
SELECT *
  FROM transactions
 WHERE status = 'active';

-- PostgREST needs explicit grants on views even when the base table is
-- already granted, because PostgREST enforces table-level privileges at
-- the schema-cache layer.
GRANT SELECT ON active_transactions TO authenticated;


-- ---------------------------------------------------------------------------
-- top_user_merchants — switch to active_transactions
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW top_user_merchants
    WITH (security_invoker = true) AS
SELECT merchant,
       COUNT(*)::int AS freq_90d,
       MAX(date)     AS last_seen
FROM active_transactions
WHERE date >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY merchant
ORDER BY COUNT(*) DESC, MAX(date) DESC
LIMIT 30;


-- ---------------------------------------------------------------------------
-- dashboard_summary(date) — re-CREATE to filter status = 'active'
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION dashboard_summary(p_today date)
RETURNS TABLE (
    category               text,
    this_month             numeric,
    monthly_baseline       numeric,
    category_tx_count      integer,
    category_history_days  integer
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_user_id     uuid := auth.uid();
    v_month_start date := date_trunc('month', p_today)::date;
    v_month_end   date := (date_trunc('month', p_today) + interval '1 month - 1 day')::date;
    v_3mo_start   date := (date_trunc('month', p_today) - interval '3 months')::date;
BEGIN
    RETURN QUERY
    SELECT
        cats.category AS category,
        COALESCE(
            (SELECT SUM(t.amount) FROM transactions t
               WHERE t.user_id = v_user_id
                 AND t.status = 'active'
                 AND t.category = cats.category
                 AND t.date >= v_month_start
                 AND t.date <= LEAST(p_today, v_month_end)),
            0
        ) AS this_month,
        COALESCE(
            (SELECT AVG(monthly_sum) FROM (
                SELECT SUM(t.amount) AS monthly_sum
                  FROM transactions t
                 WHERE t.user_id = v_user_id
                   AND t.status = 'active'
                   AND t.category = cats.category
                   AND t.date >= v_3mo_start
                   AND t.date < v_month_start
                 GROUP BY date_trunc('month', t.date)
            ) s),
            0
        ) AS monthly_baseline,
        COALESCE(
            (SELECT COUNT(*)::int FROM transactions t
               WHERE t.user_id = v_user_id
                 AND t.status = 'active'
                 AND t.category = cats.category
                 AND t.date < v_month_start),
            0
        ) AS category_tx_count,
        COALESCE(
            (SELECT (p_today - MIN(t.date))::int FROM transactions t
               WHERE t.user_id = v_user_id
                 AND t.status = 'active'
                 AND t.category = cats.category),
            0
        ) AS category_history_days
    FROM (
        SELECT DISTINCT t.category
          FROM transactions t
         WHERE t.user_id = v_user_id
           AND t.status = 'active'
    ) cats;
END;
$$;

GRANT EXECUTE ON FUNCTION dashboard_summary(date) TO authenticated;


-- ---------------------------------------------------------------------------
-- entry_moment_signals(uuid) — re-CREATE to filter status = 'active' on
-- transactions, and `status = 'active'` on cards (replaces `active = true`).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION entry_moment_signals(p_transaction_id uuid)
RETURNS TABLE (
    user_id                          uuid,
    txn_category                     text,
    txn_amount                       numeric,
    txn_date                         date,
    txn_card_id                      uuid,
    category_tx_count_prior          integer,
    category_history_days            integer,
    month_tx_count_in_category       integer,
    prior_max_in_category_this_month numeric,
    this_week_count                  integer,
    prior_4w_avg_weekly_count        numeric,
    mtd_category_spend               numeric,
    monthly_baseline_category        numeric,
    days_remaining_in_month          integer,
    this_card_name                   text,
    this_card_multiplier             numeric,
    best_card_name                   text,
    best_card_multiplier             numeric,
    wrong_card_count_this_week       integer,
    last_single_tx_notable_at        timestamptz,
    last_weekly_frequency_at         timestamptz,
    last_cumulative_delta_at         timestamptz,
    last_card_mismatch_at            timestamptz
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_user_id     uuid;
    v_category    text;
    v_amount      numeric;
    v_date        date;
    v_card_id     uuid;
    v_month_start date;
    v_month_end   date;
    v_week_start  date;
    v_3mo_start   date;
    v_4w_start    date;
BEGIN
    -- The triggering transaction is read from the base table because the
    -- entry-moment insight fires inside `POST /transactions/confirm`
    -- right after the insert; the row is `status='active'` by definition,
    -- but reading from the view would also work. Base table is fine here.
    SELECT t.user_id, t.category, t.amount, t.date, t.card_id
      INTO v_user_id, v_category, v_amount, v_date, v_card_id
      FROM transactions t
     WHERE t.id = p_transaction_id
       AND t.status = 'active';

    -- RLS denial, missing row, or already-deleted row → return nothing.
    IF v_user_id IS NULL THEN
        RETURN;
    END IF;

    v_month_start := date_trunc('month', v_date)::date;
    v_month_end   := (date_trunc('month', v_date) + interval '1 month - 1 day')::date;
    v_week_start  := v_date - INTERVAL '6 days';
    v_3mo_start   := (date_trunc('month', v_date) - interval '3 months')::date;
    v_4w_start    := v_date - INTERVAL '28 days';

    RETURN QUERY
    SELECT
        v_user_id,
        v_category,
        v_amount,
        v_date,
        v_card_id,

        -- New-user soft gate (used by both rule eligibility and dashboard).
        (SELECT COUNT(*)::int FROM transactions tx
           WHERE tx.user_id = v_user_id
             AND tx.status = 'active'
             AND tx.category = v_category
             AND tx.id <> p_transaction_id),
        COALESCE(
            (SELECT (v_date - MIN(tx.date))::int FROM transactions tx
               WHERE tx.user_id = v_user_id
                 AND tx.status = 'active'
                 AND tx.category = v_category
                 AND tx.id <> p_transaction_id),
            0
        ),

        -- Rule 1 inputs: month count + previous max (excluding this txn).
        (SELECT COUNT(*)::int FROM transactions tx
           WHERE tx.user_id = v_user_id
             AND tx.status = 'active'
             AND tx.category = v_category
             AND tx.date >= v_month_start
             AND tx.date <= v_month_end
             AND tx.id <> p_transaction_id),
        COALESCE(
            (SELECT MAX(tx.amount) FROM transactions tx
               WHERE tx.user_id = v_user_id
                 AND tx.status = 'active'
                 AND tx.category = v_category
                 AND tx.date >= v_month_start
                 AND tx.date <= v_month_end
                 AND tx.id <> p_transaction_id),
            0
        ),

        -- Rule 2 inputs: this-week count + prior-4-weeks avg weekly count.
        (SELECT COUNT(*)::int FROM transactions tx
           WHERE tx.user_id = v_user_id
             AND tx.status = 'active'
             AND tx.category = v_category
             AND tx.date >= v_week_start
             AND tx.date <= v_date),
        COALESCE(
            (SELECT COUNT(*)::numeric / 4.0 FROM transactions tx
               WHERE tx.user_id = v_user_id
                 AND tx.status = 'active'
                 AND tx.category = v_category
                 AND tx.date >= v_4w_start
                 AND tx.date < v_week_start),
            0
        ),

        -- Rule 3 inputs: MTD spend + 3-month monthly baseline + days left.
        COALESCE(
            (SELECT SUM(tx.amount) FROM transactions tx
               WHERE tx.user_id = v_user_id
                 AND tx.status = 'active'
                 AND tx.category = v_category
                 AND tx.date >= v_month_start
                 AND tx.date <= v_date),
            0
        ),
        COALESCE(
            (SELECT AVG(monthly_sum) FROM (
                SELECT SUM(tx.amount) AS monthly_sum
                  FROM transactions tx
                 WHERE tx.user_id = v_user_id
                   AND tx.status = 'active'
                   AND tx.category = v_category
                   AND tx.date >= v_3mo_start
                   AND tx.date < v_month_start
                 GROUP BY date_trunc('month', tx.date)
            ) s),
            0
        ),
        (v_month_end - v_date),

        -- Rule 4 inputs: this card's multiplier + the best card's multiplier
        -- + count of this-category-this-week transactions on a sub-best card.
        (SELECT c.name FROM cards c
           WHERE c.id = v_card_id),
        COALESCE(
            (SELECT (c.multipliers ->> v_category)::numeric FROM cards c
               WHERE c.id = v_card_id),
            1
        ),
        (SELECT c.name FROM cards c
           WHERE c.user_id = v_user_id
             AND c.status = 'active'
             AND (c.multipliers ? v_category)
           ORDER BY (c.multipliers ->> v_category)::numeric DESC, c.created_at ASC
           LIMIT 1),
        COALESCE(
            (SELECT (c.multipliers ->> v_category)::numeric FROM cards c
               WHERE c.user_id = v_user_id
                 AND c.status = 'active'
                 AND (c.multipliers ? v_category)
               ORDER BY (c.multipliers ->> v_category)::numeric DESC, c.created_at ASC
               LIMIT 1),
            0
        ),
        (SELECT COUNT(*)::int FROM transactions tx
           LEFT JOIN cards c ON c.id = tx.card_id
           WHERE tx.user_id = v_user_id
             AND tx.status = 'active'
             AND tx.category = v_category
             AND tx.date >= v_week_start
             AND tx.date <= v_date
             AND COALESCE((c.multipliers ->> v_category)::numeric, 1) < COALESCE(
                 (SELECT MAX((c2.multipliers ->> v_category)::numeric)
                    FROM cards c2
                   WHERE c2.user_id = v_user_id
                     AND c2.status = 'active'
                     AND (c2.multipliers ? v_category)),
                 1)),

        -- Rate-limit state. Each window only looks back as far as the rule's
        -- suppression period — older rows are irrelevant and the index on
        -- (user_id, rule_id, fired_at DESC) makes the lookup cheap.
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'single_tx_notable'
             AND f.category = v_category
             AND f.fired_at >= v_month_start),
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'weekly_frequency'
             AND f.category = v_category
             AND f.fired_at >= (v_date - INTERVAL '7 days')),
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'cumulative_delta'
             AND f.category = v_category
             AND f.fired_at >= (v_date - INTERVAL '7 days')),
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'card_mismatch'
             AND f.fired_at >= (v_date - INTERVAL '14 days'))
    ;
END;
$$;

GRANT EXECUTE ON FUNCTION entry_moment_signals(uuid) TO authenticated;
