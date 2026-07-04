-- conversation_distillation_message_count — 2026-07-03 (DESIGN.md §7.6, T3)
--
-- Make distillation fire for the CURRENTLY-active conversation as it
-- grows, instead of only for a prior conversation the user returns to
-- after the 10-minute idle window. Before this change, a user who tested
-- in one sitting (never coming back 10+ min later to a *different*
-- conversation) never triggered distillation at all — the piggyback
-- predicate `find_idle_undistilled_conversation` structurally excludes
-- the current conversation and requires a return-visit.
--
-- Two changes:
--
--   1. Track how many chat_messages rows existed at the last distillation
--      (`message_count`), so the current conversation can be RE-distilled
--      once it grows by REDISTILL_DELTA new messages. This SUPERSEDES the
--      original "append-only, distill each conversation exactly once"
--      posture from 20260517130000: the state row is now UPSERTed, and its
--      presence no longer means "never touch again" but "distilled through
--      message N". `distill_session` skips only when the conversation has
--      not grown by the delta since `message_count` was last written.
--      (Organic re-mentions still dedup via user_memory's unique index, so
--      re-distilling a grown conversation is cheap and idempotent.)
--
--   2. find_conversation_to_distill(conv, min, delta) — the current-turn
--      probe the chat route calls alongside the existing idle-backstop
--      probe. Returns the conversation when it has >= `min` committed
--      messages AND has grown by >= `delta` since its last distillation.
--      SECURITY INVOKER so chat_messages RLS scopes the read to the caller
--      (CLAUDE.md invariant 1) — same posture as
--      find_idle_undistilled_conversation.

ALTER TABLE conversation_distillation_state
    ADD COLUMN IF NOT EXISTS message_count integer NOT NULL DEFAULT 0;

-- distill_session now UPSERTs this row (INSERT ... ON CONFLICT DO UPDATE),
-- so RLS needs an UPDATE policy in addition to the existing SELECT/INSERT
-- policies from 20260517130000. Same owner predicate.
CREATE POLICY conversation_distillation_state_update
    ON conversation_distillation_state
    FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- Current-turn distillation probe. Returns p_conversation_id when it is
-- worth (re-)distilling now; NULL otherwise. The message-count delta lives
-- here in SQL, mirroring how the 10-minute idle threshold lives inside
-- find_idle_undistilled_conversation — no Python timer, no client hook.
CREATE OR REPLACE FUNCTION find_conversation_to_distill(
    p_conversation_id uuid,
    p_min_messages    integer,
    p_redistill_delta integer
)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT cm.conversation_id
      FROM chat_messages cm
      LEFT JOIN conversation_distillation_state cds
        ON cds.conversation_id = cm.conversation_id
     WHERE cm.user_id = auth.uid()
       AND cm.conversation_id = p_conversation_id
     GROUP BY cm.conversation_id, cds.message_count
    HAVING COUNT(*) >= p_min_messages
       AND COUNT(*) - COALESCE(cds.message_count, 0) >= p_redistill_delta
     LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION
    find_conversation_to_distill(uuid, integer, integer) TO authenticated;

-- Monotonic state-row upsert. distill_session records the message count it
-- distilled through; because two overlapping BackgroundTasks can snapshot
-- different live counts and finish out of order, a plain last-writer-wins
-- upsert could REGRESS message_count (the straggler writes its smaller
-- snapshot), which would make the next delta guard too permissive and cause
-- an extra re-distillation. GREATEST makes the write monotonic — the stored
-- count never goes backward. SECURITY INVOKER + auth.uid() so a caller can
-- only ever write its own row (RLS INSERT/UPDATE policies still apply); this
-- replaces the PostgREST `.upsert()` from the Python side, which cannot
-- express a conditional ON CONFLICT expression.
CREATE OR REPLACE FUNCTION upsert_conversation_distillation_state(
    p_conversation_id uuid,
    p_message_count   integer
)
RETURNS void
LANGUAGE sql
SECURITY INVOKER
SET search_path = public
AS $$
    INSERT INTO conversation_distillation_state
        (conversation_id, user_id, message_count, distilled_at)
    VALUES (p_conversation_id, auth.uid(), p_message_count, now())
    ON CONFLICT (conversation_id) DO UPDATE
        SET message_count = GREATEST(
                conversation_distillation_state.message_count,
                EXCLUDED.message_count
            ),
            distilled_at = now();
$$;

GRANT EXECUTE ON FUNCTION
    upsert_conversation_distillation_state(uuid, integer) TO authenticated;

-- Idle backstop, now delta-aware (Codex review, 2026-07-03). The original
-- find_idle_undistilled_conversation (migration 20260517130200) used
-- `NOT EXISTS (state row)`, which — once distillation became repeatable (T3)
-- — permanently excluded any conversation that had been distilled even once.
-- A conversation that grows past its first distillation and is then abandoned
-- (the user never returns to it to fire the current-conversation probe) would
-- have its tail lost forever. Replacing the anti-join with the SAME
-- LEFT JOIN + count-delta predicate as find_conversation_to_distill lets the
-- idle path recover that growth after the 10-minute window. It still excludes
-- the current conversation (that one is the current-conversation probe's job)
-- and still orders most-recently-idle first, draining one per turn.
--
-- Signature change (added p_min_messages / p_redistill_delta) means DROP then
-- CREATE, not CREATE OR REPLACE — the arg list is part of the function's
-- identity. Both probes are now parameterized from MIN_CONVERSATION_MESSAGES
-- and REDISTILL_DELTA in app/agent/memory.py, so the thresholds have a single
-- source of truth (no hardcoded 4 to drift). During the migrate-prod →
-- deploy-backend window the old backend calls the now-dropped 1-arg overload;
-- that errors, but `_schedule_idle_distillation` swallows it (best-effort
-- enrichment, turn continues).
DROP FUNCTION IF EXISTS find_idle_undistilled_conversation(uuid);

CREATE FUNCTION find_idle_undistilled_conversation(
    p_current_conversation_id uuid,
    p_min_messages            integer,
    p_redistill_delta         integer
)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT cm.conversation_id
      FROM chat_messages cm
      LEFT JOIN conversation_distillation_state cds
        ON cds.conversation_id = cm.conversation_id
     WHERE cm.user_id = auth.uid()
       AND cm.conversation_id IS DISTINCT FROM p_current_conversation_id
     GROUP BY cm.conversation_id, cds.message_count
    HAVING MAX(cm.created_at) < now() - interval '10 minutes'
       AND COUNT(*) >= p_min_messages
       AND COUNT(*) - COALESCE(cds.message_count, 0) >= p_redistill_delta
     ORDER BY MAX(cm.created_at) DESC
     LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION
    find_idle_undistilled_conversation(uuid, integer, integer) TO authenticated;
