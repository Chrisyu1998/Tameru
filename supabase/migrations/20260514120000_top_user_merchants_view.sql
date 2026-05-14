-- top_user_merchants — Day 9c (DESIGN.md §3.4)
-- Per-user top 30 merchants over the last 90 days, ordered by frequency then
-- recency. Read by `render_user_merchants()` to feed Claude's system-prompt
-- merchant-canonicalization block (CLAUDE.md invariant 8 — chat is the only
-- create surface, so canonicalization has to happen on the write path).
--
-- security_invoker = true is load-bearing. Postgres views default to running
-- as the view owner (SECURITY DEFINER semantics), which would bypass RLS on
-- the underlying `transactions` table and return every user's rows. With
-- security_invoker the view runs as the caller, so the per-user RLS policy
-- on transactions fires and `auth.uid() = user_id` scopes the result.
--
-- LIMIT 30 lives in the view rather than in the calling client so the
-- network round trip carries at most ~30 rows; the prompt-block token budget
-- (~300 tokens) assumes this cap.

CREATE OR REPLACE VIEW top_user_merchants
    WITH (security_invoker = true) AS
SELECT merchant,
       COUNT(*)::int AS freq_90d,
       MAX(date)     AS last_seen
FROM transactions
WHERE date >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY merchant
ORDER BY COUNT(*) DESC, MAX(date) DESC
LIMIT 30;
