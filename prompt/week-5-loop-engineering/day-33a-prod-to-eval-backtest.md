# Day 33a — Production-to-eval loop, part 1: sampling + trace-faithfulness judge + backtest

## Goal

The discovery half of the production-to-eval loop: a script samples real `chat_turn_trace` turns, grades them with a **new trace-faithfulness judge**, dedupes failure patterns against the existing corpus and pattern history, and emits a human-readable report. **No synthesis, no PRs, no schedule yet** — Day 33a ends with a backtest over the last ~30 days of production traces, and the acceptance criterion for the whole feature is you reading that report and judging whether the flagged failures are real and the patterns are ones you'd want as eval cases. If the judge precision is poor, fix it here before Day 33b builds automation on top of it.

**Why a new judge:** `judge_v1` grades eval rows that have known expected answers. Production turns have no ground truth. The trace-faithfulness judge asks a different, self-contained question: *given the tool results inside this trace, is the final prose answer consistent with them?* (Wrong aggregation, mis-read tool output, answered a different month than asked, ignored an error result.) That's gradable without ground truth — but it's a new prompt, new rubric, new false-positive modes. Budget it as such.

## Read first

- `supabase/migrations/20260424120000_chat_turn_trace.sql` — the trace shape: full Anthropic wire-shape `messages` JSONB per turn (`user` → `assistant tool_use` → `user tool_result` → … → final text), `user_id`, `conversation_id`, `seq`, owner-RLS.
- `app/prompts/judge.py` (`judge_v1`) — house style for versioned judge prompts, forced-tool scoring, `temperature=0`.
- `eval.py` `_judge_multi_hop_row` / `_normalize_judgment` — the forced-tool judging mechanics to mirror.
- The `PiiRedactionFilter` pattern set (app logging) — reused as a verification gate over everything the judge emits.
- CLAUDE.md invariant 1 + memory.md 2026-05-22 "Weekly digest cron … promoted to sanctioned service-role callers" — the enumeration ceremony this script must follow.

## Deliverables

### 1. `scripts/prod_to_eval.py` — stages 1–3 + report

**Stage 1 — sample (SQL, free).** Last `--days N` (default 7; backtest passes 30) of `chat_turn_trace`, stratified, capped at `--max-turns` (default 20; backtest 60):
- turns with ≥3 `tool_use` blocks in `messages` (multi-hop),
- turns whose `messages` contain a tool_result error payload,
- **rephrase signal**: conversations where two user-typed turns land within ~3 minutes — sample the *first* turn of the pair and hand the judge both turns (the rephrase is the strongest implicit "the answer was bad" signal; note honestly this is a heuristic join on trace adjacency, not free certainty),
- a small random control slice (~20%).

**Stage 2 — judge.** New `app/prompts/trace_judge.py` (`trace_judge_v1`): Sonnet via `ANTHROPIC_JUDGE_MODEL`, `temperature=0`, one forced `record_trace_judgment` tool call per turn returning `{faithful: bool, score: 1-5, failure_class, failure_description, hop_index}`. The prompt **instructs the judge to write `failure_description` structurally** — tool names, hop counts, category-of-question — never merchant names, amounts, dates, or quoted user text.

**Stage 3 — threshold + dedup.** Only `faithful=false` (or score ≤ threshold) turns proceed. Dedup each failure against (a) existing corpus rows (the YAML descriptions/tags in `evals/*.yaml`) and (b) the pattern history, via a **Haiku same-pattern check** — *not* embeddings: Anthropic has no embeddings API and a new vendor is a CLAUDE.md ask-first item.

**Pattern state — `evals/prod_patterns.json`** (repo-side, committed): fingerprints + structural descriptions of every pattern ever proposed or dismissed, with dates. Repo-side because it dedupes against the corpora that live next to it and the PRs it will guard live in the same repo — loop memory on disk. Stage 3 reads it; Day 33b writes it.

**Report** (`--report-out <path>`, markdown): turns sampled per stratum, judged, flagged, deduped-away; per finding — judge verdict, failure class, structural description, trace pointer (`user_id` prefix + `conversation_id` + `seq`, enough for *you* to look it up in Supabase, never the content). **Redaction gate:** run the PiiRedactionFilter pattern set over the report and the patterns file before writing; any hit fails the run. The judge is instructed to be structural; this gate is the enforcement.

### 2. Access posture

- The script reads `chat_turn_trace` cross-user with the service role — a batch job with no user JWT in scope. It lives in `scripts/` (already excluded from the leak-guard by directory), but the **enumeration ceremony still applies**: add it to CLAUDE.md invariant 1's named-caller list with a rationale, and mirror in DESIGN.md §9.1, in this PR.
- Backtest runs from your machine with prod env vars (or `railway run`-style env inheritance). The GitHub Actions schedule is Day 33b.
- Judge + dedup calls are **eval infrastructure** — billed on the eval key, not written to `ai_call_log` (same posture as `judge_v1`, recorded in DESIGN.md §7.10). They *read* user data, so one privacy-disclosure line lands now (see below) even though nothing user-derived leaves the database except structural descriptions.

### 3. Privacy disclosure (same PR)

`/privacy` page + DESIGN.md §9.6: one honest line — automated review of chat interactions for quality improvement; failure patterns are extracted in anonymized, structural form; chat content itself never leaves the database. This is true precisely because of the redaction gate; keep the wording and the mechanism in lockstep.

### 4. Tests

- Stage-1 stratification against seeded local traces (each stratum picks what it should; cap respected).
- Judge plumbing with a mocked Anthropic client (forced-tool parse, error-row skip-don't-zero — mirror `test_eval_judge.py`).
- Redaction gate: a planted merchant/amount in a fake judge description fails the run.
- Dedup: a pattern already in `prod_patterns.json` doesn't re-report.

## Don't

- Don't copy user text, merchant names, or amounts into the report, the patterns file, stdout, or anything else that leaves the database. Structural descriptions only — the redaction gate is non-negotiable, fail-closed.
- Don't reuse `judge_v1` "with a different input" — the rubric is different; version it separately so eval-judge and trace-judge drift independently.
- Don't use embeddings for dedup (new vendor; ask-first). Haiku comparison over short structural descriptions is sufficient at this volume.
- Don't build synthesis, PR automation, or the schedule in this prompt. The backtest report is the gate for all of that.
- Don't write these judge calls to `ai_call_log`, and don't bill them on the prod Anthropic key.
- Don't lower the flagging threshold to manufacture findings if the backtest comes back quiet. A quiet backtest is a real result — it bounds what Day 33b is worth.

## Done when

- `python scripts/prod_to_eval.py --days 30 --max-turns 60 --report-out /tmp/backtest.md` runs against prod traces and produces the report.
- Every flagged finding in the report carries a structural description that survives the redaction gate, plus a trace pointer you can use to inspect the real turn.
- You've read the backtest and made the explicit call: judge precision is good enough to build Day 33b on (or the gaps are named and fixed here first). Record the verdict in `memory.md`.
- CLAUDE.md invariant 1 + DESIGN.md §9.1 enumerate the new service-role caller; the privacy disclosure line is live.
- All tests green; the script passes the docstring doctrine (helpers below entry points).
