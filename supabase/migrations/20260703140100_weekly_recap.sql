-- weekly_recap — in-app weekly recap card (DESIGN.md §6.2 / §6.4)
-- Stores one composed weekly digest payload per user per local week, so the
-- "This week" card at the top of the chat screen can render (and re-render on
-- reopen) without recomputing. This is the *durable weekly artifact* that
-- relaxes the entry-moment insight's "ephemeral by design" posture — the
-- per-transaction entry bubbles stay ephemeral; only this once-a-week recap
-- persists.
--
-- Two write paths feed this table, both dedup'd on the recipient's LOCAL
-- Monday date (`dedup_week`, same key semantics as email_log — memory
-- 2026-06-01):
--   * the digest cron (service role) stores it for digest-enabled users when
--     it composes their email — no extra Sonnet call;
--   * GET /chat/recap (user JWT) composes on demand for anyone without a
--     stored row yet (digest-disabled users, or before Monday's cron fires),
--     at ≤1 Sonnet call per user per week.
--
-- The plain UNIQUE (user_id, dedup_week) — not a partial index — lets
-- PostgREST `.upsert(on_conflict="user_id,dedup_week", ignore_duplicates=True)`
-- infer the arbiter without an RPC. Recaps are point-in-time snapshots: no
-- UPDATE/DELETE policy, first writer for the week wins.

CREATE TABLE weekly_recap (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    dedup_week            date        NOT NULL,  -- recipient's local Monday
    week_start            date        NOT NULL,  -- Monday of the summarized week
    week_end              date        NOT NULL,  -- Sunday of the summarized week
    week_total            numeric     NOT NULL,
    baseline_avg          numeric     NOT NULL,  -- 8-week trailing avg
    top_category          text,
    top_category_total    numeric,
    top_category_baseline numeric,
    home_currency         text        NOT NULL,
    ui_language           text,
    observation           text        NOT NULL,
    nudge                 text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT weekly_recap_user_week_uniq UNIQUE (user_id, dedup_week)
);

CREATE INDEX weekly_recap_user_week_idx
    ON weekly_recap (user_id, dedup_week DESC);

ALTER TABLE weekly_recap ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_recap FORCE  ROW LEVEL SECURITY;

-- Owner-only read/insert. The digest cron writes via the service role, which
-- carries BYPASSRLS, so it is unaffected by these policies (same posture as
-- the cron's email_log / ai_call_log writes, CLAUDE.md invariant 1). There is
-- deliberately no UPDATE or DELETE policy — a recap is an immutable snapshot.
CREATE POLICY weekly_recap_select ON weekly_recap
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY weekly_recap_insert ON weekly_recap
    FOR INSERT
    WITH CHECK (user_id = auth.uid());
