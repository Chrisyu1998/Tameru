# Day 33b — Production-to-eval loop, part 2: synthesis + quarantine + weekly PR

## Goal

The proposal half: for each novel failure pattern Day 33a flags, synthesize an eval case in the existing YAML format with **fictional data over the existing fixtures**, verify it actually reproduces the failure class, and open **one weekly draft PR** proposing the cases. Merged cases enter the suite **quarantined** (`known_failing`) so they never brick the CI gate; un-quarantining happens in the PR that fixes the underlying bug — at which point the case becomes a permanent regression guard. The suite grows from how the product actually fails.

This closes the full loop across the week's work: production failure → quarantined eval case → `eval-regression`-adjacent issue → `claude-plan` → fix PR → un-quarantine → gate.

## Depends on

- Day 33a merged **and its backtest verdict was "build it"** — if the judge's precision wasn't validated, stop and fix that first.
- Day 30 (weekly eval must understand quarantine — see Deliverable 1).

## Read first

- `evals/multi_hop.yaml` header — the row schema synthesis must emit (`expected_tool_sequence` + `args_must_include`, `answer_pattern`, `expected_answer_value_usd` ± tolerance) and the **fixture-coupling warning**: expected values are computable only from the seeded fixture totals (Dining Jan $150 / Feb $200 / Mar $250, Netflix + Spotify, the Amex Gold card…).
- `scripts/_eval_setup.py` + `scripts/seed_eval_fixtures.py` — the fixture dataset synthesis targets.
- `eval.py` scoring + threshold roll-up — where quarantine exclusion lands.
- `.github/workflows/ci.yml` `eval-gate` local-stack steps — cloned for reproduction verification.

## Deliverables

### 1. Quarantine support in `eval.py` (land this first — nothing merges without it)

- Optional row key `known_failing: "<issue-or-PR ref>"`. Quarantined rows **run and report** (own line in the summary table: `quarantined: n passed / m total`) but are **excluded from gate and target math**. Rationale in the row schema docs: every case this loop produces is *verified to fail* — counting it would turn the suite red on every unrelated PR.
- A quarantined row that **passes** prints a loud nudge: "quarantined row <id> now passes — un-quarantine it." (The un-quarantine edit itself stays human, in the fix PR.)
- `evals/README.md`: document `known_failing` + the lifecycle (born quarantined → bug fixed → flag removed → permanent regression guard) + `tags: [prod-derived]` convention.

### 2. Stage 4 — synthesis + reproduction verification (`scripts/prod_to_eval.py`)

- Per novel pattern: one Sonnet call (forced tool emitting the YAML row as structured JSON) generating a **structurally identical, fictionally populated** case targeting the *existing* fixtures — the real trace's content never appears. If a pattern can't be expressed against existing fixtures, it goes in the PR body's "needs manual fixture work" section; the loop **never** edits `_eval_setup.py` itself (fixture totals are load-bearing for every existing expected value).
- **Reproduction verification:** run the candidate through the real agent loop against the local stack (clone the eval-gate steps: `supabase start` → migrate → seed → mint JWT) **3 times; require ≥2 failures** (agent non-determinism). A candidate that passes when production failed missed the trigger → "needs manual review" section, not a proposed case.
- Redaction gate (Day 33a's) runs over every generated case, description, and the PR body. Fail-closed.

### 3. Stage 5 — weekly PR + dismissal memory

- One branch/PR per week (`prod-to-eval/week-<ISO week>`), **draft**, label `prod-to-eval`: the new YAML rows (each `known_failing` + `tags: [prod-derived]`), the updated `evals/prod_patterns.json`, and a body with per-case structural descriptions, judge scores, repro results (2/3 etc.), plus the needs-review sections. Never auto-merges.
- **Dismissal memory:** each PR body embeds a machine-readable fingerprint block. The weekly run lists prior `prod-to-eval` PRs via the GitHub API: **closed-unmerged** → fingerprints recorded as dismissed (never re-proposed); **merged** → already in `prod_patterns.json` via the merge itself. Closing a PR *is* the dismissal — no separate UI.

### 4. `.github/workflows/prod-to-eval.yml`

- `schedule:` weekly (clear of prod-health 11:00 UTC and weekly-eval) + `workflow_dispatch`. Needs prod read secrets (service-role key, as prod-health already wires) **and** the local-stack steps; eval-key billing; `contents: write` + `pull-requests: write`.
- **Quiet-week skip:** fewer than ~8 sampled turns → log "skipped: insufficient traffic," exit 0, no PR. Don't lower the bar to manufacture findings. (At ~10 users this will fire often — the weekly cadence proves the loop; the Day 33a backtest was the harvest.)
- Failure of the *pipeline itself* files a deduped issue (label `prod-to-eval-failure`), prod-health pattern.

### 5. Docs (same PR)

- DESIGN.md §7.10: the production-to-eval pipeline, the quarantine lifecycle, the synthetic-reconstruction privacy stance (real traces never leave the DB; cases are fictional reconstructions verified to reproduce the failure class).

## Don't

- Don't merge any case un-quarantined. Born-failing is the contract; the gate must stay green on unrelated PRs.
- Don't let synthesis touch `scripts/_eval_setup.py` or existing YAML rows — additive only, fixtures frozen.
- Don't auto-merge, and don't open more than one PR per week (review burden is the budget).
- Don't put real merchant names, amounts, dates, or quoted user text anywhere in the PR — the redaction gate guards this; treat a gate trip as a bug in the judge/synthesis prompts, not something to whitelist.
- Don't propose a case whose reproduction verification didn't fail ≥2/3 — unreproduced patterns are findings for a human, not cases.
- Don't write any of this loop's LLM calls to `ai_call_log`; eval-key billing, same as Day 33a.

## Done when

- A seeded local failure pattern (force a known agent bug or use a backtest finding) flows end-to-end: flagged → synthesized → reproduced 2/3+ → draft PR with a quarantined, `prod-derived`-tagged YAML row whose expected values check out against the fixture totals by hand.
- With the new row merged, `python eval.py --eval=multi_hop` reports it under the quarantined line and gate math is unchanged; CI on an unrelated PR stays green.
- Fixing the underlying bug and removing `known_failing` flips the row into gate math; re-introducing the bug now breaches the gate (the regression-guard lifecycle works).
- Closing a test PR unmerged causes the next run to record its fingerprints as dismissed and not re-propose them.
- A quiet week (stub the sample) exits 0 with the skip notice and no PR.
- DESIGN.md §7.10 documents the pipeline + quarantine lifecycle in the same PR.
