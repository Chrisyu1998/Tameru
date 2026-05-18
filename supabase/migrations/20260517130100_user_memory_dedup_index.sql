-- user_memory dedup unique index + upsert RPC — Day 16 (DESIGN.md §7.6 layer 2)
--
-- Two coupled artifacts: a unique index over (user_id, category, lower(fact))
-- and a SECURITY INVOKER RPC that performs the actual upsert. They ship
-- together because the RPC's ON CONFLICT clause references the index by
-- expression — splitting them would let one land without the other.
--
-- Why not PostgREST's built-in upsert? PostgREST's `on_conflict` query
-- parameter accepts a list of column names and matches them against
-- unique constraints — it cannot target an expression index like
-- `lower(fact)`. We could normalize fact text in Python before insert and
-- key the index on the raw `fact` column, but that puts a load-bearing
-- invariant in the application layer instead of the schema. An RPC keeps
-- the dedup contract enforced at the DB.

-- Within-category dedup only — same fact landing under two categories on
-- different distillations is two rows by design (a signal worth seeing,
-- and Day 17's capacity cap will prune the weaker of the two).
CREATE UNIQUE INDEX user_memory_dedup
    ON user_memory (user_id, category, lower(fact));


-- upsert_user_memory_fact — INSERT a new fact or update an existing one.
--
-- SECURITY INVOKER so user_memory's RLS scopes the write to the caller's
-- JWT. CLAUDE.md invariant 14 — distillation runs from a request handler
-- under the user's JWT, never the service role.
--
-- Conflict target is the (user_id, category, lower(fact)) expression
-- index. On conflict:
--   * reinforced_at advances to now() (drives Day 17's time-decay sweep).
--   * relevance_score moves up but never down via GREATEST(...) — a
--     momentary low-confidence re-extraction must not downgrade a high-
--     confidence past assessment.
--
-- Returns the resulting row's id so callers can chain follow-on writes
-- (e.g. a future "audit which conversation reinforced which fact" table).
CREATE OR REPLACE FUNCTION upsert_user_memory_fact(
    p_fact            text,
    p_category        text,
    p_relevance_score numeric
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_id uuid;
BEGIN
    INSERT INTO user_memory (user_id, fact, category, relevance_score)
    VALUES (auth.uid(), p_fact, p_category, p_relevance_score)
    ON CONFLICT (user_id, category, lower(fact))
    DO UPDATE SET
        reinforced_at   = now(),
        relevance_score = GREATEST(user_memory.relevance_score, EXCLUDED.relevance_score)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

GRANT EXECUTE ON FUNCTION upsert_user_memory_fact(text, text, numeric) TO authenticated;
