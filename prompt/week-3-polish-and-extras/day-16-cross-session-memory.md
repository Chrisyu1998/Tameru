# Day 16 — Cross-session memory: distillation, injection, and Settings panel

## Goal

After a chat conversation has been idle for 10 minutes, a Claude Haiku call distills atomic facts from its transcript into `user_memory`, exactly once per `conversation_id`. On every chat turn, those facts are rendered into the **dynamic** half of the system prompt (block[1]). Settings panel lets the user view, edit, and delete memories.

Distillation is triggered by a **piggyback check** on the next chat turn — not by a client-side timer, not by `beforeunload`, not by a pg_cron sweep. The next turn pays the price of distilling any prior idle conversation. This keeps the JWT fresh (no expired-token problem) and removes the need for any new orchestration primitive.

## Read first

- `DESIGN.md` §7.6 (session memory — both layers), §8.5 (`user_memory` schema), §8.11 (`chat_messages` / `conversation_id` semantics — there is no separate `conversations` table), §11.3 (system-prompt cache breakpoint — the memory block must live **after** it).
- `app/prompts/chat.py` — `render_system_prompt` returns a two-block content array with `cache_control: ephemeral` on block[0]. Memory goes in block[1].

## Deliverables

### Migrations

- `..._conversation_distillation_state.sql`:
  - New table `conversation_distillation_state(conversation_id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE, distilled_at timestamptz NOT NULL DEFAULT now())`.
  - One row per conversation, ever. The row's presence is the signal "this conversation has been distilled — never distill it again."
  - RLS: `FOR ALL` scoped to `user_id = auth.uid()` — same shape as `chat_messages` (§8.11).
  - Index: `(user_id, distilled_at DESC)` — supports the piggyback predicate's anti-join.

- `..._user_memory_dedup_index.sql`:
  - `CREATE UNIQUE INDEX user_memory_dedup ON user_memory (user_id, category, lower(fact));`
  - Enables `INSERT ... ON CONFLICT (user_id, category, lower(fact)) DO UPDATE SET reinforced_at = now(), relevance_score = GREATEST(user_memory.relevance_score, EXCLUDED.relevance_score)`. Within-category dedup only — a fact landing under two different categories on different distillations is two rows, by design (Day 17's cap will sort it out).

### Backend

- `app/agent/memory.py`:
  - `distill_session(user_jwt, conversation_id)`:
    - Loads `chat_messages` for the conversation via the user's JWT.
    - **Short-conversation skip:** if row count < 4 (one user + one assistant pair both directions), return early — no Haiku call, no `conversation_distillation_state` row written (so a longer follow-up in the same conversation can trigger distillation later).
    - Calls Claude Haiku with: *"Extract atomic facts about this user that should persist across sessions. Categories: `spending_pattern | preference | active_context | card_preference | goal`. Score each fact 0–1 by enduring relevance. Return JSON list of `{fact, category, relevance_score}`."*
    - For each returned fact: `INSERT INTO user_memory ... ON CONFLICT (user_id, category, lower(fact)) DO UPDATE SET reinforced_at = now(), relevance_score = GREATEST(user_memory.relevance_score, EXCLUDED.relevance_score)`.
    - On success, `INSERT INTO conversation_distillation_state (conversation_id, user_id)` — marks the conversation done so the piggyback predicate will skip it forever.
    - Writes one `ai_call_log` row with `task_type='memory_distill'`, `model='claude-haiku-4-5'`, `prompt_version='memory_distill_v1'`, success/error.
    - Wrapped in `try/except`. On any exception: log to Sentry, do NOT insert the `conversation_distillation_state` row, do NOT propagate. The next piggyback firing will retry.
  - `render_user_memory(user_jwt) -> str`:
    - `SELECT fact, category FROM user_memory ORDER BY relevance_score DESC, reinforced_at DESC LIMIT 60`.
    - Returns the block as a bulleted string headed `What I know about this user:` with `- [category] fact text` per line, then a trailing blank line.
    - Returns `""` if no rows (caller concatenates blindly).
    - Catches DB errors, logs to Sentry, returns `""` — a chat turn must not 500 because of a memory read failure (parity with `render_user_merchants`).

- `app/routes/memory.py`:
  - `GET /memory?limit=60&offset=0` → list facts ordered by `relevance_score DESC, reinforced_at DESC`.
  - `PATCH /memory/{id}` body `{fact?, relevance_score?}` → updates the row and sets `reinforced_at = now()` (manual edit counts as reinforcement per §7.6 — keeps Day 17's time-decay sweep from pruning what the user just curated).
  - `DELETE /memory/{id}` → hard delete. A future distillation that re-extracts the same fact will recreate the row with a new `id` — that's intended; the user's delete was about *this version* of the fact at *this moment*, not a permanent lifetime ban.

### Wire-in

- `app/prompts/chat.py`:
  - Bump `PROMPT_VERSION` to `chat_v7` and add a chat_v7 entry to the version-log docstring naming this change.
  - In `render_system_prompt`, append `render_user_memory(user_jwt)` to the **block[1] dynamic tail**, after the merchants block. Do NOT touch block[0] — per-user memory inside the cached preamble would invalidate the prefix cache for every user and break §11.3's cost projection.
  - `system_prompt_hash` already hashes block[0] only — no change needed; the chat_v7 bump alone makes the new hash distinct from chat_v6 in `ai_call_log.prompt_hash`.

- `app/routes/chat.py`:
  - At the top of `POST /chat/turn`, before the SSE generator returns, run the piggyback check:
    ```sql
    SELECT cm.conversation_id
      FROM chat_messages cm
     WHERE cm.user_id = auth.uid()
       AND cm.conversation_id <> :current_conversation_id
       AND NOT EXISTS (
         SELECT 1 FROM conversation_distillation_state cds
          WHERE cds.conversation_id = cm.conversation_id
       )
     GROUP BY cm.conversation_id
    HAVING MAX(cm.created_at) < now() - interval '10 minutes'
     ORDER BY MAX(cm.created_at) DESC
     LIMIT 1;
    ```
    If a row is returned, `background_tasks.add_task(distill_session, user.jwt, that_conversation_id)`. The JWT in the closure is the current turn's JWT, fresh by definition.
  - The piggyback check is non-fatal: any error is logged to Sentry and the turn proceeds normally.

### Frontend

- `frontend/src/pages/Settings.tsx`:
  - New section "What Tameru remembers about you", grouped by category.
  - Per row: fact text (inline editable), category badge, relevance score (read-only number), `×` delete button with confirm-on-tap.
  - Empty state: *"Tameru hasn't learned anything about you yet. The more you chat, the better it gets."*
- In the chat UI: small footer link "View memory" → `/settings#memory`.

### Tests

- `tests/test_memory_distill.py`: synthetic 6-message conversation mentioning a goal and a card preference → assert two `user_memory` rows are inserted with the right `category` values; assert a `conversation_distillation_state` row exists for the conversation_id.
- `tests/test_memory_distill_reinforcement.py`: pre-seed a fact, run distillation on a conversation that re-mentions it → assert the existing row's `reinforced_at` advances and no second row is created (the dedup unique index catches it via `ON CONFLICT`).
- `tests/test_memory_distill_skip_short.py`: `distill_session` called on a conversation with 3 `chat_messages` rows → assert no Haiku call was made, no `user_memory` row written, no `conversation_distillation_state` row written.
- `tests/test_memory_distill_idempotent.py`: call `distill_session` twice for the same `conversation_id` → second call no-ops because the piggyback predicate excludes already-distilled conversations (and even if forced, the dedup index prevents duplicate facts).
- `tests/test_memory_injection.py`: seed three `user_memory` rows → assert the rendered system prompt has the fact text in `block[1]["text"]` and not in `block[0]["text"]`. Deleting one of the rows and re-rendering removes its text from block[1].
- `tests/test_memory_injection_failure.py`: monkeypatch `render_user_memory` to raise → assert `render_system_prompt` still returns a valid two-block array with no memory section and the chat turn does not 500.
- `tests/test_memory_piggyback.py`: seed an undistilled conversation whose latest message is 11 min old; fire a chat turn against a different conversation_id → assert a BackgroundTask was scheduled with the stale conversation_id. Vary: latest message 9 min old → no task scheduled.

## Don't

- Don't use a vector DB. `ORDER BY relevance_score DESC, reinforced_at DESC LIMIT 60` is the retrieval strategy.
- Don't include memories the user has deleted. DELETE is hard delete. If a later distillation re-extracts the same fact, the row reappears — that's the intended contract.
- Don't put the memory block in `render_system_prompt` block[0]. That invalidates the §11.3 prompt-cache discount for every user.
- Don't fire distillation from a `POST /chat/end_session` endpoint or a `beforeunload` hook. The piggyback on the next chat turn is the only trigger in v1.
- Don't add a 10-minute idle timer in Python or the browser. The threshold is enforced in SQL on the piggyback query.
- Don't dedup memories across categories — within-category only.
- Don't run distillation synchronously in the chat turn path. Always `BackgroundTasks.add_task` so it runs after the SSE response completes.

## Done when

- `tests/test_memory_distill.py` passes: a 6-message conversation about a CSR Q2 SUB goal produces a `user_memory` row with `category='goal'` whose `fact` contains "CSR".
- `tests/test_memory_injection.py` passes: `render_system_prompt` for a user with a seeded fact returns a block list whose `block[1]["text"]` contains the fact verbatim and whose `block[0]["text"]` does not.
- `tests/test_memory_injection.py` also passes: after `DELETE /memory/{id}`, the fact no longer appears in block[1].
- `tests/test_memory_distill_skip_short.py` passes: a 3-row conversation triggers no Haiku call.
- `tests/test_memory_piggyback.py` passes: the 10-minute threshold gate works on both sides.
- `PROMPT_VERSION` is `chat_v7`; `system_prompt_hash` on the new prompt body differs from any chat_v6 hash captured in `ai_call_log`.
