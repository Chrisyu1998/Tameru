# Day 17 — Memory time decay + capacity cap

## Goal

`user_memory` stays bounded: facts older than 90 days without reinforcement are pruned; over 60 facts triggers a relevance-based pruning pass.

## Read first

- `DESIGN.md` §7.6 (memory cleanup rules).

## Deliverables

- New migration `..._memory_cleanup_function.sql`:
  - SQL function `prune_user_memory()`:
    - Deletes rows where `reinforced_at < now() - interval '90 days'`.
    - For users with > 60 remaining facts, keeps the top 60 by `relevance_score`, deletes the rest.
  - `SELECT cron.schedule('prune-memory', '0 3 * * *', 'SELECT prune_user_memory();');` — runs daily at 03:00 UTC.
- Backend:
  - `app/agent/memory.py` — when distillation produces a new fact and the user is at the 60 cap, score the new fact and the lowest-scoring existing fact via Claude Haiku ("Which is more enduring?"). Keep the winner. Avoids the "useful new fact dropped because cron hasn't run" race.
- Frontend:
  - In Settings → Memory, show: "Memory: X / 60 facts. Oldest fact reinforced N days ago."
- Tests:
  - `tests/test_memory_cleanup.py`:
    - Seed 65 facts with varying `relevance_score` and `reinforced_at`. Run prune. Assert 60 remain, the right 60.
    - Seed 5 facts older than 90 days with no reinforcement. Run prune. Assert all 5 deleted.

## Don't

- Don't decay facts the user has manually edited (treat manual edit as reinforcement). Update `reinforced_at` on `PATCH /memory/{id}`.
- Don't run the prune cron in dev. Production only.

## Done when

- Seeding 100 facts and running prune leaves exactly 60.
- A 91-day-stale fact is gone; an 89-day-stale fact remains.
- A user at 60 facts who triggers distillation with a new high-relevance fact ends up with 60 facts including the new one (lowest old one bumped).
