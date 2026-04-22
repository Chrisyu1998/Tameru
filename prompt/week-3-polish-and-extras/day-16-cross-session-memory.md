# Day 16 — Cross-session memory: distillation, injection, and Settings panel

## Goal

After every chat session ends, a Claude Haiku call distills atomic facts from the transcript into `user_memory`. On every new chat turn, those facts are injected into the system prompt. Settings panel lets the user view, edit, and delete memories.

## Read first

- `DESIGN.md` §7.6 (session memory — both layers), §8.5 (`user_memory` schema).

## Deliverables

- Backend:
  - `app/agent/memory.py`:
    - `distill_session(user_jwt, conversation_id)` — loads the full conversation, calls Claude Haiku with a system prompt like: "Extract atomic facts about this user that should persist across sessions. Categories: spending_pattern, preference, active_context, card_preference, goal. Score each fact 0–1 by enduring relevance. Return JSON list."
    - Upserts each fact into `user_memory` with `reinforced_at = now()`. If a fact's text already exists (case-insensitive match within the same category), update `reinforced_at` instead of inserting.
    - Logged to `ai_call_log` with `task_type="memory_distill"`.
  - `render_user_memory(user_jwt) -> str`: pulls top 60 facts ordered by `relevance_score * recency_weight`, formats as a bullet list under a "What I know about this user:" header. Returns the string to inject into Claude's system prompt.
  - Trigger distillation: when a chat session has been idle for 10 minutes (no new message), or explicitly on `POST /chat/end_session`. Use a short-lived background task (asyncio task) — failure is non-fatal, log to Sentry.
  - `app/routes/memory.py`:
    - `GET /memory` → list facts (paginated).
    - `PATCH /memory/{id}` → edit fact text or relevance.
    - `DELETE /memory/{id}` → remove a fact.
- Frontend:
  - `frontend/src/pages/Settings.tsx` — add a "What Tameru remembers about you" section showing all facts with edit and delete buttons.
  - In the chat UI, add a small footer link: "View memory" → opens Settings.
- Wire `render_user_memory()` into the system prompt of `app/agent/loop.py`. Cache the rendered block per turn (not per loop iteration) to avoid recomputing across the agent loop.
- Tests:
  - `tests/test_memory_distill.py`: synthetic conversation → assert facts extracted with right categories.
  - `tests/test_memory_injection.py`: seeded `user_memory` rows → assert they appear in the rendered system prompt.

## Don't

- Don't use a vector DB. Top-60 by score is fine.
- Don't include memories in the prompt that the user has deleted (`DELETE` is hard delete).
- Don't distill on every turn — only at session end. Otherwise costs and latency explode.

## Done when

- After a chat session that mentions "I'm trying to hit my CSR $4K spend by Q2," the next session's first turn shows Claude is aware of the goal.
- Settings panel lists the fact with its category and score.
- Deleting the fact removes it from the next session's prompt.
