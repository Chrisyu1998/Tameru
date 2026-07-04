-- entry_moment warm-up + positive rules (DESIGN.md §6.2)
-- Adds three rules to the entry-moment engine so it has something honest to
-- say during a new user's first month (before the ≥6-tx/≥30-day soft gate that
-- rules 2 & 3 depend on can clear) and so it can occasionally say something
-- *positive* instead of only warnings:
--
--   * category_share    — this purchase is a large share of the month's spend
--                         in its category (gate-free; no baseline claimed).
--   * largest_this_week — biggest single purchase this week across categories
--                         (gate-free).
--   * pacing_under      — projected category spend comfortably under baseline
--                         (soft-gate required — it *does* claim a baseline).
--
-- Also loosens the card_mismatch rate limit from once-per-14-days *globally*
-- to once-per-14-days *per category*, so a rewards-focused user hears the
-- optimization nudge for each mis-carded category rather than one at a time.
--
-- Base body is the CURRENT definition from `20260516150000` (status-column
-- soft-delete: every transactions subquery filters `status = 'active'` and
-- cards use `status = 'active'`, NOT the pre-soft-delete `active = true`) — the
-- new subqueries inherit the same `status = 'active'` filter.

-- 1. Widen the rule_id CHECK for the three new rule ids.
ALTER TABLE entry_moment_fires DROP CONSTRAINT entry_moment_fires_rule_check;
ALTER TABLE entry_moment_fires ADD CONSTRAINT entry_moment_fires_rule_check
    CHECK (rule_id IN (
        'single_tx_notable',
        'weekly_frequency',
        'cumulative_delta',
        'card_mismatch',
        'category_share',
        'largest_this_week',
        'pacing_under'
    ));

-- 2. Replace entry_moment_signals with the extended signal set. The RETURNS
-- TABLE shape changes (new trailing columns + per-category card_mismatch
-- window), so this is DROP + CREATE, not CREATE OR REPLACE. SECURITY INVOKER
-- is preserved so per-user RLS on transactions/cards/entry_moment_fires still
-- scopes every read to the caller's JWT.
DROP FUNCTION entry_moment_signals(uuid);

CREATE FUNCTION entry_moment_signals(p_transaction_id uuid)
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
    last_card_mismatch_at            timestamptz,
    week_tx_count_all                integer,
    week_prior_max_all               numeric,
    last_category_share_at           timestamptz,
    last_largest_this_week_at        timestamptz,
    last_pacing_under_at             timestamptz
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
        -- card_mismatch is now scoped per category (was global) so each
        -- mis-carded category earns its own nudge within the 14-day window.
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'card_mismatch'
             AND f.category = v_category
             AND f.fired_at >= (v_date - INTERVAL '14 days')),

        -- Warm-up rule inputs: this-week count + prior max across ALL
        -- categories (largest_this_week). category_share reuses
        -- mtd_category_spend + txn_amount, so it needs no new signal here.
        (SELECT COUNT(*)::int FROM transactions tx
           WHERE tx.user_id = v_user_id
             AND tx.status = 'active'
             AND tx.date >= v_week_start
             AND tx.date <= v_date),
        COALESCE(
            (SELECT MAX(tx.amount) FROM transactions tx
               WHERE tx.user_id = v_user_id
                 AND tx.status = 'active'
                 AND tx.date >= v_week_start
                 AND tx.date <= v_date
                 AND tx.id <> p_transaction_id),
            0
        ),

        -- Rate-limit state for the three new rules.
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'category_share'
             AND f.category = v_category
             AND f.fired_at >= (v_date - INTERVAL '7 days')),
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'largest_this_week'
             AND f.fired_at >= (v_date - INTERVAL '7 days')),
        (SELECT MAX(f.fired_at) FROM entry_moment_fires f
           WHERE f.user_id = v_user_id
             AND f.rule_id = 'pacing_under'
             AND f.category = v_category
             AND f.fired_at >= (v_date - INTERVAL '14 days'))
    ;
END;
$$;

-- Make the function callable via PostgREST RPC from the user's JWT.
GRANT EXECUTE ON FUNCTION entry_moment_signals(uuid) TO authenticated;
