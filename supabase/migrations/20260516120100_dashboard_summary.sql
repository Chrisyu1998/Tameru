-- dashboard_summary — Day 13 (DESIGN.md §6.3)
-- Returns one row per category that the user has ever spent in, plus the
-- aggregates the dashboard needs to compute deltas, color-code tiles, and
-- evaluate the soft new-user gate (≥6 prior transactions AND ≥30 days of
-- history per category).
--
-- p_today is supplied by the caller rather than read from now()::date so
-- the dashboard always uses the user's local "today" (the FastAPI handler
-- looks up users_meta.home_currency / timezone if multi-tz support ever
-- lands). Today, transactions.date is already user-local (CLAUDE.md
-- invariant 13), so callers pass CURRENT_DATE.
--
-- SECURITY INVOKER so per-user RLS on `transactions` scopes the aggregates
-- to the caller — a leaked function name cannot expose another user's data.

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
                 AND t.category = cats.category
                 AND t.date < v_month_start),
            0
        ) AS category_tx_count,
        COALESCE(
            (SELECT (p_today - MIN(t.date))::int FROM transactions t
               WHERE t.user_id = v_user_id
                 AND t.category = cats.category),
            0
        ) AS category_history_days
    FROM (
        SELECT DISTINCT t.category
          FROM transactions t
         WHERE t.user_id = v_user_id
    ) cats;
END;
$$;

GRANT EXECUTE ON FUNCTION dashboard_summary(date) TO authenticated;
