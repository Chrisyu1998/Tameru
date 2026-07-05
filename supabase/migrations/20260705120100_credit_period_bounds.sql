-- credit_period_bounds() — calendar-anchored period math (DESIGN.md §6.7, §8.17)
--
-- Single source of truth for a credit's period boundaries, consumed by BOTH:
--   * card_credits_confirm() (migration 20260705120200) — to SEED
--     current_period_start / next_reset_date at confirm / manual-add time;
--   * reset_card_credits() (migration 20260705120300) — to ADVANCE them when
--     today crosses a boundary.
-- Keeping the math in one SQL function means there is no Python↔SQL mirror to
-- drift. It is also the field the Phase-2 ledger-bridge period guard
-- (`WHERE :tx_date >= current_period_start`) depends on.
--
-- Cadences are calendar-anchored in v1 (§6.7): monthly = the 1st; quarterly =
-- Jan/Apr/Jul/Oct 1; semiannual = Jan 1 / Jul 1; annual = Jan 1. Anniversary /
-- cardmember-year anchoring is deferred.
--
-- IMMUTABLE (no clock read): the caller resolves "today in the user's timezone"
-- and passes it as p_on_date, so this function is pure calendar arithmetic on a
-- date. date_trunc is applied to `::timestamp` (not timestamptz) so it resolves
-- the IMMUTABLE 2-arg overload — the timestamptz form is only STABLE
-- (memory.md 2026-05-25).

CREATE OR REPLACE FUNCTION credit_period_bounds(
    p_cadence text,
    p_on_date date
)
RETURNS TABLE(period_start date, next_reset date)
LANGUAGE sql
IMMUTABLE
AS $$
    WITH s AS (
        SELECT (CASE p_cadence
            WHEN 'monthly'    THEN date_trunc('month',   p_on_date::timestamp)::date
            WHEN 'quarterly'  THEN date_trunc('quarter', p_on_date::timestamp)::date
            WHEN 'semiannual' THEN make_date(
                                       EXTRACT(year FROM p_on_date)::int,
                                       CASE WHEN EXTRACT(month FROM p_on_date) <= 6 THEN 1 ELSE 7 END,
                                       1)
            WHEN 'annual'     THEN date_trunc('year',    p_on_date::timestamp)::date
        END) AS ps
    )
    SELECT
        s.ps AS period_start,
        (CASE p_cadence
            WHEN 'monthly'    THEN s.ps + INTERVAL '1 month'
            WHEN 'quarterly'  THEN s.ps + INTERVAL '3 months'
            WHEN 'semiannual' THEN s.ps + INTERVAL '6 months'
            WHEN 'annual'     THEN s.ps + INTERVAL '1 year'
        END)::date AS next_reset
    FROM s;
$$;

COMMENT ON FUNCTION credit_period_bounds(text, date) IS
  'DESIGN.md §6.7 — calendar-anchored (period_start, next_reset) for a credit '
  'cadence given a user-local date. Single source of truth for both the confirm '
  'seed (card_credits_confirm) and the reset advance (reset_card_credits).';

-- Pure math, no table access — safe under the default authenticated EXECUTE
-- grant (backfill in 20260515210000). No REVOKE needed; it leaks nothing.
