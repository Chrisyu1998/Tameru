-- find_idle_undistilled_conversation — Day 16 (DESIGN.md §7.6 layer 2)
--
-- Used by POST /chat/turn to detect a prior conversation that is past
-- the 10-minute idle threshold and has not yet been distilled. If the
-- RPC returns a row, the chat route schedules a BackgroundTask to
-- distill it. The 10-minute threshold lives here in SQL — there is no
-- Python timer, no client-side beforeunload hook, no pg_cron sweep.
--
-- SECURITY INVOKER so chat_messages's RLS scopes the read to the
-- caller's JWT. CLAUDE.md invariant 1 — handler-path reads use the
-- user's JWT, never the service role.
--
-- Returns the SINGLE most-recently-idle conversation per call. If the
-- user has multiple idle conversations queued, each chat turn drains
-- one. v1's expected volume per user (a few conversations at most)
-- makes this fine; a "drain all in one turn" variant can come later if
-- the queue ever stacks up.
--
-- Short-conversation gate: HAVING includes COUNT(*) >= 4 so a 1-3
-- message conversation is invisible to this probe. Without it, the
-- Python short-circuit in `distill_session` returns early without
-- writing `conversation_distillation_state`, so the same short
-- conversation gets re-selected on every later turn and starves
-- longer eligible conversations behind it. The 4 here must stay in
-- sync with `MIN_CONVERSATION_MESSAGES` in `app/agent/memory.py`.

CREATE OR REPLACE FUNCTION find_idle_undistilled_conversation(
    p_current_conversation_id uuid
)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT cm.conversation_id
      FROM chat_messages cm
     WHERE cm.user_id = auth.uid()
       AND cm.conversation_id IS DISTINCT FROM p_current_conversation_id
       AND NOT EXISTS (
         SELECT 1
           FROM conversation_distillation_state cds
          WHERE cds.conversation_id = cm.conversation_id
       )
     GROUP BY cm.conversation_id
    HAVING MAX(cm.created_at) < now() - interval '10 minutes'
       AND COUNT(*) >= 4
     ORDER BY MAX(cm.created_at) DESC
     LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION find_idle_undistilled_conversation(uuid) TO authenticated;
