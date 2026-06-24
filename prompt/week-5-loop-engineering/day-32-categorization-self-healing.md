# Day 32 — Categorization self-healing (product-side loop, zero LLM calls)

## Goal

A weekly product loop that finds historical transactions whose stored `category` contradicts the user's own `merchant_category` override for the same merchant, and proposes a batch fix the user confirms with one tap. Discovery is pure SQL; the digest email surfaces "N transactions look miscategorized" with a deep link; an in-app review screen applies the batch `PATCH` on the user's tap. This is propose-then-confirm (invariant 8) running in-product on a schedule — the product-side echo of the dev loops, and the symmetry Day 34 writes up.

**Signal correction vs. the original sketch:** transactions do **not** store Gemini's confidence (`transactions` has `merchant`/`category`/`source` only — confidence is returned by `categorize()` but never persisted). The finding signal is purely the **contradiction**: an active transaction whose category ≠ the override's category for the same (canonicalized) merchant. That's also the higher-precision signal — the override is the user's own stated truth.

## Read first

- `supabase/migrations/20260421120400_merchant_category.sql` — the override table: `UNIQUE (user_id, merchant)`, most-recent-correction-wins.
- The merchant canonicalization path from Day 09c (`app/services/` — whatever normalizer the categorization prompt path uses). The contradiction join must use the **same** normalization, or "Trader Joes" vs "Trader Joe's" produces both false positives and false negatives.
- `app/services/digest.py` `compose_digest` + DESIGN.md §6.4 — the ≤5-content-block ceiling and the **optional nudge** slot (see Deliverable 3).
- `memory.md` 2026-05-17 "`committed_payload` rehydrate annotation: pull-on-read" — why batch edits need no chat-history back-patching (chat parse cards are a current-state view; the fix flows through for free).
- CLAUDE.md "Verifying user-facing changes" — the review screen needs a real nav path on both surfaces, not just the deep link.

## Deliverables

### 1. Findings as a pure function — no new cron, no new service

`app/services/hygiene.py::find_categorization_findings(client, *, limit=50) -> list[CategorizationFinding]`:

- One SQL pass (via PostgREST or a SECURITY INVOKER RPC if the join wants server-side SQL): active transactions joined to `merchant_category` on the canonicalized merchant where categories differ, **excluding** dismissed findings (Deliverable 2), ordered most-recent-first, capped at `limit`.
- Pydantic model: `{transaction_id, merchant, date, amount, current_category, proposed_category}`.
- Runs under **whichever client is passed**: the route passes the user-JWT client (RLS scopes it); the digest compose passes its existing service-role client (the digest cron is already a sanctioned invariant-1 caller — this adds reads to an existing sanctioned job, **no new service-role caller, no invariant amendment**). This is why there's no new cron: the digest's weekly per-user fire *is* the schedule.

### 2. Dismissal memory — migration `hygiene_dismissals`

- `(id, user_id FK auth.users ON DELETE CASCADE, transaction_id, proposed_category, created_at)`, `UNIQUE (user_id, transaction_id, proposed_category)`, owner-RLS (`FOR ALL USING/WITH CHECK auth.uid() = user_id`), FORCE RLS.
- Keyed on `(transaction_id, proposed_category)` so a dismissal stops *that* proposal recurring, but a **new** override (different proposed category) re-surfaces the transaction.

### 3. Routes (`app/routes/hygiene.py`)

- `GET /hygiene/findings` — computes on demand under the user's JWT, returns the list + count. On-demand means digest-disabled users still get findings when they open the screen.
- `POST /hygiene/fix` — body: list of `{transaction_id, proposed_category}`. Applies `UPDATE transactions SET category = ...` per row under the user's JWT (RLS enforces ownership). This is the invariant-8 commit: an explicit HTTP call from a user tap. Validate each `proposed_category` against the category taxonomy; reject rows whose transaction no longer matches the finding (category changed since computation) rather than blind-writing.
- `POST /hygiene/dismiss` — same body shape; inserts dismissal rows, `ON CONFLICT DO NOTHING`.

### 4. Digest section (amends `compose_digest`)

- When `find_categorization_findings` returns ≥1 for the recipient, the digest's **optional-nudge block** becomes: "N transactions look miscategorized — review and fix in Tameru", linked to `${FRONTEND_ORIGIN}/hygiene?source=digest`. It **occupies** the existing nudge slot (≤5-block ceiling untouched); when a spending nudge and findings both exist, findings win the slot (the spending signal already has the dashboard + entry-moment surfaces).
- Plaintext version mirrors it. Same `?source=digest` convention as Day 26b (the landing already fires `weekly_digest_opened`; no new PostHog event for v1 — `feature_used` with `{feature: "hygiene_fix"}` on the fix tap is allowed since it's structural, but **no category names, no counts of money, no merchants** in props).

### 5. Review screen (frontend)

- Route `/hygiene`: list of finding cards (merchant, date, amount, current → proposed category), per-row checkbox defaulting checked, **Fix selected** (calls `/hygiene/fix`) and **Dismiss selected** (calls `/hygiene/dismiss`), empty state ("Nothing needs fixing").
- **Nav reachability on both surfaces** (CLAUDE.md doctrine — the deep link alone is not a nav path): a conditional banner ("N suggested category fixes") on the breakdown/category page when findings exist, on both desktop and mobile layouts, linking to `/hygiene`. Verify by walking each surface's own nav from cold start — no deep-linking in verification.

### 6. Tests + docs

- Route tests: contradiction found; canonicalization match; dismissal suppresses; new proposed category re-surfaces; fix updates and is idempotent; stale finding rejected; RLS cross-user denial (extend `tests/contracts/test_rls.py` table list for `hygiene_dismissals` — mind the 2026-06-07 session-fixture learning: write fixture-preserving values).
- Digest compose test: findings present → nudge slot contains the hygiene line; absent → prior behavior byte-identical.
- DESIGN.md sync (same PR): short §6.x "Categorization self-healing" + the §6.4 nudge-slot amendment.

## Don't

- Don't add a cron service or a pg_cron job for this. The digest fire + on-demand compute covers both delivery paths; a third scheduler is pure operational surface.
- Don't auto-apply fixes (no silent UPDATE from compose, no apply-on-open). The user's tap is the commit — invariant 8 is the feature, not an obstacle.
- Don't backfill `merchant_category` from this flow's UI. The override table's write path stays the edit sheet; this loop only *reads* overrides.
- Don't put merchant names, amounts, or category names into PostHog props or log lines (PiiRedactionFilter posture applies — the findings list is exactly the content it redacts).
- Don't add a dashboard tile for findings (invariant 9). The banner + digest + screen are the surfaces.
- Don't store Gemini confidence on transactions "while we're here" — schema change with no consumer; the contradiction signal needs no score.

## Done when

- A user with a `merchant_category` override contradicting ≥1 active transaction sees the banner on the breakdown page (both nav surfaces, walked from cold start), opens `/hygiene`, taps Fix selected, and the transactions' categories update — visible in the per-category list and in chat-rehydrated parse cards without further writes.
- Dismissed findings stay gone across recomputes; a changed override re-surfaces the transaction.
- `python -m app.cron.digest --user <id> --dry-run` for a user with findings prints the hygiene nudge line in HTML + plaintext; for a user without findings, output is unchanged from before this PR.
- Full backend pytest + frontend vitest green; RLS contract test covers `hygiene_dismissals`.
- DESIGN.md carries the new section and §6.4 amendment in the same PR.
